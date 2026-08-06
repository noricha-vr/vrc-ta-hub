#!/usr/bin/env bash
# 本番DBの未適用 migration を一覧する（read-only）。
#
# Cloud Run Job で showmigrations --plan を実行し、その実行のログだけを読んで
# `[ ]` 行（未適用）を出す。ログが1行も取れない場合は「未適用ゼロ」ではなく
# エラーで落とす。取り込み遅延を「問題なし」と誤認すると、未適用のまま
# トラフィックを流す事故（DatabaseCache のテーブル不在で全ページ 500）に直結する。
#
# 出力:
#   未適用あり -> 該当行を stdout に出して exit 1
#   未適用なし -> "no pending migrations" を出して exit 0
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-vrc-ta-hub}"
REGION="${REGION:-asia-northeast1}"
JOB_NAME="${JOB_NAME:-vrc-ta-hub-migrate}"
# Cloud Logging は書き込み直後に読めないことがあるため、取得できるまで待つ
LOG_RETRIES="${LOG_RETRIES:-10}"
LOG_RETRY_INTERVAL_SEC="${LOG_RETRY_INTERVAL_SEC:-6}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 2
}

command -v gcloud >/dev/null 2>&1 || die "gcloud CLI not found."

gcloud run jobs describe "$JOB_NAME" --project="$PROJECT_ID" --region="$REGION" >/dev/null 2>&1 \
  || die "Cloud Run job $JOB_NAME not found. Create it with ./scripts/create_migrate_job.sh"

# `gcloud run jobs execute --args` による実行時上書きは、この環境では API 側が
# overrides を受け付けない（Unknown name "priorityTier"）。Job 定義を一時的に
# showmigrations へ差し替えて実行し、終了時に必ず元へ戻す。
ORIGINAL_ARGS="$(
  gcloud run jobs describe "$JOB_NAME" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --format='value[delimiter="|"](spec.template.spec.template.spec.containers[0].args)'
)"
[[ -n "$ORIGINAL_ARGS" ]] || die "Could not read current args of $JOB_NAME."

restore_args() {
  gcloud run jobs update "$JOB_NAME" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --args="^|^${ORIGINAL_ARGS}" >/dev/null 2>&1 \
    || printf 'WARNING: failed to restore args of %s to "%s"\n' "$JOB_NAME" "$ORIGINAL_ARGS" >&2
}
trap restore_args EXIT

gcloud run jobs update "$JOB_NAME" \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --args='^|^manage.py|showmigrations|--plan' >/dev/null 2>&1 \
  || die "Failed to set showmigrations args on $JOB_NAME."

gcloud run jobs execute "$JOB_NAME" \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --wait >/dev/null 2>&1 \
  || die "Failed to execute $JOB_NAME."

EXECUTION="$(
  gcloud run jobs executions list \
    --job="$JOB_NAME" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --limit=1 \
    --format='value(name)'
)"
[[ -n "$EXECUTION" ]] || die "Could not determine the execution name for $JOB_NAME."

LOG_LINES=""
for _ in $(seq 1 "$LOG_RETRIES"); do
  LOG_LINES="$(
    gcloud logging read \
      "labels.\"run.googleapis.com/execution_name\"=\"$EXECUTION\"" \
      --project="$PROJECT_ID" \
      --limit=500 \
      --format='value(textPayload)' 2>/dev/null || true
  )"
  # showmigrations --plan は必ず [X] か [ ] の行を出す。1行も無ければ未取得とみなす
  if printf '%s' "$LOG_LINES" | grep -qE '^\[[X ]\]'; then
    break
  fi
  LOG_LINES=""
  sleep "$LOG_RETRY_INTERVAL_SEC"
done

[[ -n "$LOG_LINES" ]] \
  || die "No migration plan found in logs for $EXECUTION. Treating this as unknown, not as zero pending."

PENDING="$(printf '%s\n' "$LOG_LINES" | grep -E '^\[ \]' || true)"
if [[ -n "$PENDING" ]]; then
  printf 'pending migrations:\n%s\n' "$PENDING"
  exit 1
fi

printf 'no pending migrations\n'
