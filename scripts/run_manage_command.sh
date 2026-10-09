#!/usr/bin/env bash
# Cloud Run Job の引数を一時的に差し替え、manage.py の実行ログを出力する。
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-vrc-ta-hub}"
REGION="${REGION:-asia-northeast1}"
JOB_NAME="${JOB_NAME:-vrc-ta-hub-migrate}"
LOG_RETRIES="${LOG_RETRIES:-10}"
LOG_RETRY_INTERVAL_SEC="${LOG_RETRY_INTERVAL_SEC:-6}"
WAIT_RETRIES="${WAIT_RETRIES:-360}"
WAIT_INTERVAL_SEC="${WAIT_INTERVAL_SEC:-10}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 2
}

# 算術式に入る値は数字だけ受け付ける（bash は算術式の中の文字列を評価する）
for name in LOG_RETRIES LOG_RETRY_INTERVAL_SEC WAIT_RETRIES WAIT_INTERVAL_SEC; do
  [[ "${!name}" =~ ^[0-9]{1,4}$ ]] || die "$name must be a number."
done

[[ $# -gt 0 ]] || die "Usage: $0 <manage.py arguments...>"
COMMAND_ARGS='manage.py'
for arg in "$@"; do
  [[ "$arg" != *'|'* ]] || die 'Arguments must not contain |.'
  COMMAND_ARGS+="|$arg"
done

command -v gcloud >/dev/null 2>&1 || die 'gcloud CLI not found.'
ORIGINAL_ARGS="$(
  gcloud run jobs describe "$JOB_NAME" \
    --project="$PROJECT_ID" --region="$REGION" \
    --format='value[delimiter="|"](spec.template.spec.template.spec.containers[0].args)'
)" || die "Could not describe $JOB_NAME."
[[ -n "$ORIGINAL_ARGS" ]] || die "Could not read current args of $JOB_NAME."

restore_args() {
  local status=$?
  trap - EXIT
  if ! gcloud run jobs update "$JOB_NAME" \
    --project="$PROJECT_ID" --region="$REGION" \
    --args="^|^${ORIGINAL_ARGS}" >/dev/null 2>&1; then
    printf 'ERROR: failed to restore args of %s.\n' "$JOB_NAME" >&2
    status=2
  fi
  exit "$status"
}
trap restore_args EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

gcloud run jobs update "$JOB_NAME" \
  --project="$PROJECT_ID" --region="$REGION" \
  --args="^|^${COMMAND_ARGS}" >/dev/null \
  || die "Failed to set manage.py args on $JOB_NAME."

# 最新の execution を list すると別の実行を拾うため、この execute の戻り値を使う。
# --wait は失敗した実行の名前を返さないので、--async で名前を先に取ってから完了を待つ。
EXECUTION="$(
  gcloud run jobs execute "$JOB_NAME" \
    --project="$PROJECT_ID" --region="$REGION" \
    --async --format='value(metadata.name)'
)" || die "Failed to start $JOB_NAME."
[[ -n "$EXECUTION" ]] || die "Could not determine the execution name for $JOB_NAME."

SUCCEEDED=''
for ((attempt = 1; attempt <= WAIT_RETRIES; attempt++)); do
  STATE="$(
    gcloud run jobs executions describe "$EXECUTION" \
      --project="$PROJECT_ID" --region="$REGION" \
      --format='value[separator=","](status.completionTime,status.succeededCount,status.failedCount)' 2>/dev/null || true
  )"
  IFS=, read -r COMPLETED SUCCEEDED_COUNT FAILED_COUNT <<< "$STATE" || true
  if [[ -n "${COMPLETED:-}" ]]; then
    SUCCEEDED=no
    if [[ "${SUCCEEDED_COUNT:-0}" =~ ^[1-9][0-9]*$ && ! "${FAILED_COUNT:-0}" =~ ^[1-9] ]]; then
      SUCCEEDED=yes
    fi
    break
  fi
  sleep "$WAIT_INTERVAL_SEC"
done
[[ -n "$SUCCEEDED" ]] || die "Timed out waiting for $EXECUTION."

# 取り込み途中のログで終えないよう、空でない同じ内容が 2 回続けて取れるまで読む。
LOG_LINES=''
PREVIOUS=''
for ((attempt = 1; attempt <= LOG_RETRIES; attempt++)); do
  LOG_LINES="$(
    gcloud logging read \
      "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"$JOB_NAME\" AND labels.\"run.googleapis.com/execution_name\"=\"$EXECUTION\" AND (logName=\"projects/$PROJECT_ID/logs/run.googleapis.com%2Fstdout\" OR logName=\"projects/$PROJECT_ID/logs/run.googleapis.com%2Fstderr\")" \
      --project="$PROJECT_ID" --order=asc --limit=10000 \
      --format='value(textPayload)' 2>/dev/null || true
  )"
  [[ -z "$LOG_LINES" || "$LOG_LINES" != "$PREVIOUS" ]] || break
  PREVIOUS="$LOG_LINES"
  LOG_LINES=''
  if ((attempt < LOG_RETRIES)); then
    sleep "$LOG_RETRY_INTERVAL_SEC"
  fi
done
[[ -n "$LOG_LINES" ]] || die "Command output in logs for $EXECUTION was empty or still changing."
printf '%s\n' "$LOG_LINES"
[[ "$SUCCEEDED" == yes ]] || die "Failed to execute $JOB_NAME ($EXECUTION)."
