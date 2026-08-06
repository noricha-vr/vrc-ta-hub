#!/usr/bin/env bash
# Cloud Run Job `vrc-ta-hub-migrate` の作成/更新スクリプト（冪等）。
#
# Cloud Build は Django migration を自動実行しない方針（docs/research/issue-464-cloud-run-job-migration.md）。
# 本番 migration はこの Job を人間の判断で実行して適用する。
#
# 稼働中の Cloud Run サービスからイメージ・環境変数・シークレット・SA を引き継ぐため、
# サービス側の設定を変えても Job 定義がずれない（値をこのファイルにハードコードしない）。
#
# 使い方:
#   ./scripts/create_migrate_job.sh                 # 稼働中リビジョンのイメージで作成/更新
#   IMAGE=<explicit image> ./scripts/create_migrate_job.sh
#   gcloud run jobs execute vrc-ta-hub-migrate --region=asia-northeast1 --wait
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-vrc-ta-hub}"
REGION="${REGION:-asia-northeast1}"
SERVICE_NAME="${SERVICE_NAME:-vrc-ta-hub}"
JOB_NAME="${JOB_NAME:-vrc-ta-hub-migrate}"

# Job のデフォルト引数は「全アプリの migrate」。個別 migration を当てる時は
# `gcloud run jobs execute ... --args=...` で実行時に上書きする。
# 区切りに ^|^ を使うのは、既定のカンマ区切りだと "manage.py migrate" のような
# カンマ非依存の引数列が壊れる（manage.py,migrate が別扱いされない）ため。
DEFAULT_ARGS='^|^manage.py|migrate|--noinput'

# migration の多重実行を防ぐ（リトライで同じ migration が並走しないこと優先）
MAX_RETRIES=0
TASK_TIMEOUT="10m"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

command -v gcloud >/dev/null 2>&1 || die "gcloud CLI not found."

describe_service() {
  gcloud run services describe "$SERVICE_NAME" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --format="$1"
}

if [[ -z "${IMAGE:-}" ]]; then
  IMAGE="$(describe_service 'value(spec.template.spec.containers[0].image)')" \
    || die "Could not read image from Cloud Run service $SERVICE_NAME."
fi
[[ -n "$IMAGE" ]] || die "Cloud Run service $SERVICE_NAME has no container image."

SERVICE_ACCOUNT="$(describe_service 'value(spec.template.spec.serviceAccountName)')"

# 環境変数の値をログ・プロセス引数に出さないため、--env-vars-file に一時ファイルで渡す。
# gcloud の出力もファイル経由にする。`python3 -` はスクリプト本体を stdin から読むため、
# ヒアドキュメントとパイプ入力が衝突して JSON が届かない。
ENV_VARS_FILE="$(mktemp)"
SERVICE_ENV_FILE="$(mktemp)"
trap 'rm -f "$ENV_VARS_FILE" "$SERVICE_ENV_FILE"' EXIT
chmod 600 "$ENV_VARS_FILE" "$SERVICE_ENV_FILE"

describe_service 'json(spec.template.spec.containers[0].env)' > "$SERVICE_ENV_FILE" \
  || die "Could not read env from Cloud Run service $SERVICE_NAME."

# env は plain 値と secret 参照が混在する。plain は値を含むので --env-vars-file（一時ファイル）へ、
# secret 参照は値を含まないので "ENV=SECRET:VERSION" の文字列として stdout へ出す。
SET_SECRETS="$(
  ENV_VARS_FILE="$ENV_VARS_FILE" SERVICE_ENV_FILE="$SERVICE_ENV_FILE" python3 - <<'PY'
import json
import os

with open(os.environ["SERVICE_ENV_FILE"], encoding="utf-8") as src:
    doc = json.load(src) or {}
containers = doc.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
env = (containers[0].get("env") if containers else None) or []

plain = []
secrets = []
for entry in env:
    name = entry["name"]
    ref = (entry.get("valueFrom") or {}).get("secretKeyRef")
    if ref:
        secrets.append("{}={}:{}".format(name, ref["name"], ref.get("key", "latest")))
    elif "value" in entry:
        # gcloud の env-vars-file は YAML。値の解釈ゆれを避けるため JSON 文字列としてクォートする
        # （JSON は YAML 1.2 のサブセットなので安全に読める）。
        plain.append("{}: {}".format(json.dumps(name), json.dumps(entry["value"])))

with open(os.environ["ENV_VARS_FILE"], "w", encoding="utf-8") as f:
    f.write("\n".join(plain) + "\n")

print(",".join(secrets))
PY
)"

if gcloud run jobs describe "$JOB_NAME" --project="$PROJECT_ID" --region="$REGION" >/dev/null 2>&1; then
  ACTION="update"
else
  ACTION="create"
fi

# イメージの CMD は supervisord（Dockerfile 参照）なので --command で python を明示する。
GCLOUD_ARGS=(
  run jobs "$ACTION" "$JOB_NAME"
  --project="$PROJECT_ID"
  --region="$REGION"
  --image="$IMAGE"
  --command=python
  --args="$DEFAULT_ARGS"
  --tasks=1
  --max-retries="$MAX_RETRIES"
  --task-timeout="$TASK_TIMEOUT"
  --env-vars-file="$ENV_VARS_FILE"
)

if [[ -n "$SET_SECRETS" ]]; then
  GCLOUD_ARGS+=(--set-secrets="$SET_SECRETS")
fi
if [[ -n "$SERVICE_ACCOUNT" ]]; then
  GCLOUD_ARGS+=(--service-account="$SERVICE_ACCOUNT")
fi

printf '%s Cloud Run Job %s (image: %s)\n' "$ACTION" "$JOB_NAME" "$IMAGE"
gcloud "${GCLOUD_ARGS[@]}"

printf 'Done. Execute with: gcloud run jobs execute %s --region=%s --project=%s --wait\n' \
  "$JOB_NAME" "$REGION" "$PROJECT_ID"
