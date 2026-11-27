#!/usr/bin/env bash
# Cloud Run Job `vrc-ta-hub-migrate` の作成/更新スクリプト（冪等）。
#
# Cloud Build は Django migration を自動実行しない方針（docs/research/issue-464-cloud-run-job-migration.md）。
# 本番 migration はこの Job を人間の判断で実行して適用する。
#
# 稼働中の Cloud Run サービスからイメージ・環境変数・シークレット・SA を引き継ぐため、
# サービス側の設定を変えても Job 定義がずれない（値をこのファイルにハードコードしない）。
# 既存 Job の DB 接続（KEEP_JOB_ENV）は、平文・シークレット参照の形を含めて残す。
#
# 使い方:
#   ./scripts/create_migrate_job.sh                 # 稼働中リビジョンのイメージで作成/更新
#   IMAGE=<explicit image> ./scripts/create_migrate_job.sh
#   gcloud run jobs execute vrc-ta-hub-migrate --region=asia-northeast1 --wait
#
# 撮影の扱い（recording_policy）の当て直し: デプロイのトラフィックが 100% になった後に一度流す。
# デプロイ中に旧リビジョンが受けた申請の【動画撮影】の回答を拾う（冪等）。--since には event 0032 を
# 流した時刻を渡す。execute --args は使えないので Job の引数を差し替えて実行し、最後に必ず戻す。
#   gcloud run jobs update vrc-ta-hub-migrate --region=asia-northeast1 --project=vrc-ta-hub \
#     --args='^|^manage.py|backfill_recording_policy|--since|<ISO 日時>|--dry-run'
#   gcloud run jobs execute vrc-ta-hub-migrate --region=asia-northeast1 --project=vrc-ta-hub --wait
#   （ログで件数を確かめたら --dry-run を外して update → execute をもう一度）
#   gcloud run jobs update vrc-ta-hub-migrate --region=asia-northeast1 --project=vrc-ta-hub \
#     --args='^|^manage.py|migrate|--noinput'
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-vrc-ta-hub}"
REGION="${REGION:-asia-northeast1}"
SERVICE_NAME="${SERVICE_NAME:-vrc-ta-hub}"
JOB_NAME="${JOB_NAME:-vrc-ta-hub-migrate}"
# 空文字列の指定では保持せず、すべてサービスから引き継ぐ。
KEEP_JOB_ENV="${KEEP_JOB_ENV-DB_USER DB_PASSWORD DB_HOST}"

# Job のデフォルト引数は「全アプリの migrate」。個別 migration を当てる時は
# `gcloud run jobs update ... --args=...` で差し替えて実行し、終わったら戻す
# （execute --args による実行時上書きはこの環境では使えない。docs/deployment.md 参照）。
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

is_immutable_image() {
  local img="$1"
  [[ "$img" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] || return 1
  local before_at="${img%@*}"
  local after_slash="${before_at##*/}"
  [[ "$after_slash" != *:* ]]
}

strip_image_tag_or_digest() {
  local img="${1%@*}"
  local after_slash="${img##*/}"
  if [[ "$after_slash" == *:* ]]; then
    printf '%s\n' "${img%:*}"
  else
    printf '%s\n' "$img"
  fi
}

describe_service() {
  gcloud run services describe "$SERVICE_NAME" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --format="$1"
}

resolve_immutable_image() {
  local latest_ready latest_created raw_digest rev_image repo
  latest_ready="$(describe_service 'value(status.latestReadyRevisionName)')" \
    || die "Could not read latestReadyRevisionName from Cloud Run service $SERVICE_NAME."
  latest_created="$(describe_service 'value(status.latestCreatedRevisionName)')" \
    || die "Could not read latestCreatedRevisionName from Cloud Run service $SERVICE_NAME."
  if [[ -z "$latest_ready" || -z "$latest_created" || "$latest_created" != "$latest_ready" ]]; then
    die "Latest created revision (${latest_created:-<none>}) does not match latest ready revision (${latest_ready:-<none>}) on $SERVICE_NAME. Wait for the new revision to become ready or pass IMAGE=<repo>@sha256:<digest> explicitly."
  fi

  raw_digest="$(
    gcloud run revisions describe "$latest_ready" \
      --project="$PROJECT_ID" \
      --region="$REGION" \
      --format='value(status.imageDigest)'
  )" || die "Could not read imageDigest from Cloud Run revision $latest_ready."

  if [[ -n "$raw_digest" ]]; then
    if [[ "$raw_digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
      rev_image="$(
        gcloud run revisions describe "$latest_ready" \
          --project="$PROJECT_ID" \
          --region="$REGION" \
          --format='value(spec.containers[0].image)'
      )" || die "Could not read container image from Cloud Run revision $latest_ready."
      repo="$(strip_image_tag_or_digest "$rev_image")"
      raw_digest="${repo}@${raw_digest}"
    fi
    if is_immutable_image "$raw_digest"; then
      printf '%s\n' "$raw_digest"
      return 0
    fi
    die "Revision $latest_ready returned invalid imageDigest: $raw_digest"
  fi

  rev_image="$(
    gcloud run revisions describe "$latest_ready" \
      --project="$PROJECT_ID" \
      --region="$REGION" \
      --format='value(spec.containers[0].image)'
  )" || die "Could not read container image from Cloud Run revision $latest_ready."
  if is_immutable_image "$rev_image"; then
    printf '%s\n' "$rev_image"
    return 0
  fi

  die "Could not resolve an immutable @sha256 image digest for $SERVICE_NAME (revision: $latest_ready). Pass IMAGE=<repo>@sha256:<digest> explicitly."
}

if [[ -z "${IMAGE:-}" ]]; then
  IMAGE="$(resolve_immutable_image)"
fi
is_immutable_image "$IMAGE" \
  || die "IMAGE must be an immutable @sha256:<64hex> reference, got: ${IMAGE:-<empty>}"

SERVICE_ACCOUNT="$(describe_service 'value(spec.template.spec.serviceAccountName)')"

# 環境変数の値をログ・プロセス引数に出さないため、--env-vars-file に一時ファイルで渡す。
# gcloud の出力もファイル経由にする。`python3 -` はスクリプト本体を stdin から読むため、
# ヒアドキュメントとパイプ入力が衝突して JSON が届かない。
ENV_VARS_FILE="$(mktemp)"
SERVICE_ENV_FILE="$(mktemp)"
JOB_ENV_FILE="$(mktemp)"
trap 'rm -f "$ENV_VARS_FILE" "$SERVICE_ENV_FILE" "$JOB_ENV_FILE"' EXIT
chmod 600 "$ENV_VARS_FILE" "$SERVICE_ENV_FILE" "$JOB_ENV_FILE"

describe_service 'json(spec.template.spec.containers[0].env)' > "$SERVICE_ENV_FILE" \
  || die "Could not read env from Cloud Run service $SERVICE_NAME."

if gcloud run jobs describe "$JOB_NAME" --project="$PROJECT_ID" --region="$REGION" \
  --format='json(spec.template.spec.template.spec.containers[0].env)' > "$JOB_ENV_FILE" 2>/dev/null; then
  ACTION="update"
else
  ACTION="create"
fi

# env は plain 値と secret 参照が混在する。plain は値を含むので --env-vars-file（一時ファイル）へ、
# secret 参照は値を含まないので "ENV=SECRET:VERSION" の文字列として stdout へ出す。
SET_SECRETS="$(
  ENV_VARS_FILE="$ENV_VARS_FILE" SERVICE_ENV_FILE="$SERVICE_ENV_FILE" \
    JOB_ENV_FILE="$JOB_ENV_FILE" ACTION="$ACTION" KEEP_JOB_ENV="$KEEP_JOB_ENV" python3 - <<'PY'
import json
import os
import sys

with open(os.environ["SERVICE_ENV_FILE"], encoding="utf-8") as src:
    doc = json.load(src) or {}
containers = doc.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
env = (containers[0].get("env") if containers else None) or []

keep = os.environ["KEEP_JOB_ENV"].split()
if os.environ["ACTION"] == "update" and keep:
    with open(os.environ["JOB_ENV_FILE"], encoding="utf-8") as src:
        job = json.load(src) or {}
    containers = (
        job.get("spec", {}).get("template", {}).get("spec", {})
        .get("template", {}).get("spec", {}).get("containers", [])
    )
    job_env = (containers[0].get("env") if containers else None) or []
    # サービス側の同名項目は除き、Job にある項目だけを元の形で残す。
    env = [entry for entry in env if entry["name"] not in keep]
    kept = [entry for entry in job_env if entry["name"] in keep]
    env.extend(kept)
    kept_names = {entry["name"] for entry in kept}
    if kept_names:
        names = dict.fromkeys(name for name in keep if name in kept_names)
        print("Kept from job: " + " ".join(names), file=sys.stderr)

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

# 平文が無い時は空のままにする（空の env-vars-file は gcloud が受け付けない）。
with open(os.environ["ENV_VARS_FILE"], "w", encoding="utf-8") as f:
    if plain:
        f.write("\n".join(plain) + "\n")

print(",".join(secrets))
PY
)"

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
)

# 平文が 1 件も無ければ、更新時は既存の平文を消す（全部シークレット参照に切り替わった時）。
if [[ -s "$ENV_VARS_FILE" ]]; then
  GCLOUD_ARGS+=(--env-vars-file="$ENV_VARS_FILE")
elif [[ "$ACTION" == "update" ]]; then
  GCLOUD_ARGS+=(--clear-env-vars)
fi
# シークレット参照も同じく、0 件の更新では既存の参照を消す（残すと対象外の資格情報が Job に渡る）。
if [[ -n "$SET_SECRETS" ]]; then
  GCLOUD_ARGS+=(--set-secrets="$SET_SECRETS")
elif [[ "$ACTION" == "update" ]]; then
  GCLOUD_ARGS+=(--clear-secrets)
fi
if [[ -n "$SERVICE_ACCOUNT" ]]; then
  GCLOUD_ARGS+=(--service-account="$SERVICE_ACCOUNT")
fi

printf '%s Cloud Run Job %s (image: %s)\n' "$ACTION" "$JOB_NAME" "$IMAGE"
gcloud "${GCLOUD_ARGS[@]}"

printf 'Done. Execute with: gcloud run jobs execute %s --region=%s --project=%s --wait\n' \
  "$JOB_NAME" "$REGION" "$PROJECT_ID"
