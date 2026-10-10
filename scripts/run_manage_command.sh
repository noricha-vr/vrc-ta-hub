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
# 指定すると、この文字列で始まる行がログに現れるまで成功にしない（コマンドの完了の印）
EXPECT_LOG_PREFIX="${EXPECT_LOG_PREFIX:-}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 2
}

# 算術式に入る値は数字だけ受け付ける（bash は算術式の中の文字列を評価する）
for name in LOG_RETRIES LOG_RETRY_INTERVAL_SEC WAIT_RETRIES WAIT_INTERVAL_SEC; do
  [[ "${!name}" =~ ^(0|[1-9][0-9]{0,3})$ ]] || die "$name must be a decimal number."
done

[[ $# -gt 0 ]] || die "Usage: $0 <manage.py arguments...>"
COMMAND_ARGS='manage.py'
for arg in "$@"; do
  [[ "$arg" != *'|'* ]] || die 'Arguments must not contain |.'
  COMMAND_ARGS+="|$arg"
done

command -v gcloud >/dev/null 2>&1 || die 'gcloud CLI not found.'
# shellcheck source=scripts/job_args_lock.sh
source "$(dirname "${BASH_SOURCE[0]}")/job_args_lock.sh"
acquire_job_args_lock || exit 2
trap release_job_args_lock EXIT
ORIGINAL_ARGS="$(
  gcloud run jobs describe "$JOB_NAME" \
    --project="$PROJECT_ID" --region="$REGION" \
    --format='value[delimiter="|"](spec.template.spec.template.spec.containers[0].args)'
)" || die "Could not describe $JOB_NAME."
[[ -n "$ORIGINAL_ARGS" ]] || die "Could not read current args of $JOB_NAME."
# Job の引数は共有。ほかの実行が差し替えている最中（既定の引数でない）なら、取り違えないよう断る。
IDLE_ARGS="${IDLE_ARGS:-manage.py|migrate|--noinput}"
[[ "$ORIGINAL_ARGS" == "$IDLE_ARGS" ]] \
  || die "Args of $JOB_NAME are \"$ORIGINAL_ARGS\", not \"$IDLE_ARGS\". Another run may be in progress; retry after it finishes."

restore_args() {
  local status=$?
  trap - EXIT
  if ! gcloud run jobs update "$JOB_NAME" \
    --project="$PROJECT_ID" --region="$REGION" \
    --args="^|^${ORIGINAL_ARGS}" >/dev/null 2>&1; then
    printf 'ERROR: failed to restore args of %s.\n' "$JOB_NAME" >&2
    status=2
  fi
  # 復元し終えてからロックを放す（先に放すと、次の実行の差し替えを消しうる）
  release_job_args_lock
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

# 差し替えから execute までの間に別の実行が引数を変えると、意図と違うコマンドが動く。
# その実行が使った引数を確かめ、違えばログを出した上で失敗にする。
EXECUTION_ARGS="$(
  gcloud run jobs executions describe "$EXECUTION" \
    --project="$PROJECT_ID" --region="$REGION" \
    --format='value[delimiter="|"](spec.template.spec.containers[0].args)' 2>/dev/null || true
)"
# 取れなかった時（空）も「確かめられなかった」として失敗にする
ARGS_MISMATCH=''
[[ "$EXECUTION_ARGS" == "$COMMAND_ARGS" ]] || ARGS_MISMATCH=yes

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
  if [[ -n "$LOG_LINES" && "$LOG_LINES" == "$PREVIOUS" ]]; then
    [[ -z "$EXPECT_LOG_PREFIX" ]] && break
    has_marker=''
    while IFS= read -r line; do
      [[ "$line" != "$EXPECT_LOG_PREFIX"* ]] || has_marker=1
    done <<< "$LOG_LINES"
    [[ -z "$has_marker" ]] || break
  fi
  PREVIOUS="$LOG_LINES"
  LOG_LINES=''
  if ((attempt < LOG_RETRIES)); then
    sleep "$LOG_RETRY_INTERVAL_SEC"
  fi
done
if [[ -z "$LOG_LINES" ]]; then
  # 出せる分は出してから失敗にする（取り込み途中・完了の印なし）
  [[ -z "$PREVIOUS" ]] || printf '%s\n' "$PREVIOUS"
  [[ -z "$ARGS_MISMATCH" ]] \
    || printf 'ERROR: %s ran with "%s", not the requested "%s".\n' "$EXECUTION" "${EXECUTION_ARGS:-<unknown>}" "$COMMAND_ARGS" >&2
  die "Command output in logs for $EXECUTION was empty, still changing, or missing ${EXPECT_LOG_PREFIX:-output}."
fi
printf '%s\n' "$LOG_LINES"
[[ -z "$ARGS_MISMATCH" ]] \
  || die "$EXECUTION ran with \"${EXECUTION_ARGS:-<unknown>}\", not the requested \"$COMMAND_ARGS\". Check what it changed."
[[ "$SUCCEEDED" == yes ]] || die "Failed to execute $JOB_NAME ($EXECUTION)."
