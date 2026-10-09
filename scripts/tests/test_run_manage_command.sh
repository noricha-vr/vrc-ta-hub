#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
export CALLS_FILE="$TMP_DIR/calls.log"
export LOG_COUNT_FILE="$TMP_DIR/log-count"
export WAIT_COUNT_FILE="$TMP_DIR/wait-count"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_contains() {
  grep -Fq -- "$2" "$1" || fail "Expected '$2' in $1"
}

cat > "$TMP_DIR/gcloud" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'CALL\n' >> "$CALLS_FILE"
printf '%s\n' "$@" >> "$CALLS_FILE"
printf 'config=%s active=%s\n' "${CLOUDSDK_CONFIG:-}" "${CLOUDSDK_ACTIVE_CONFIG_NAME:-}" >> "$CALLS_FILE"
case "$*" in
  'run jobs describe '*)
    [[ "$MOCK_CASE" != describe_failure ]] || exit 1
    [[ "$MOCK_CASE" != empty_args ]] || exit 0
    printf 'manage.py|migrate|--noinput\n'
    ;;
  'run jobs update '*)
    for arg in "$@"; do
      if [[ "$arg" == '--args=^|^manage.py|migrate|--noinput' ]]; then
        [[ "$MOCK_CASE" != restore_failure ]] || exit 1
        exit 0
      fi
    done
    [[ "$MOCK_CASE" != update_failure ]] || exit 1
    ;;
  'run jobs execute '*)
    [[ "$MOCK_CASE" != empty_execution ]] || exit 0
    # 実際の gcloud は --wait で失敗すると実行名を出さずに落ちる。--async だけが名前を返す。
    [[ "$*" == *--async* ]] || exit 1
    [[ "$MOCK_CASE" != execute_failure ]] || exit 1
    printf 'job-execution-this-call\n'
    ;;
  'run jobs executions describe job-execution-this-call '*)
    waits=$(cat "$WAIT_COUNT_FILE")
    waits=$((waits + 1))
    printf '%s\n' "$waits" > "$WAIT_COUNT_FILE"
    [[ "$MOCK_CASE" != never_completes ]] || exit 0
    if [[ "$MOCK_CASE" == slow_execution && "$waits" -eq 1 ]]; then
      exit 0
    fi
    if [[ "$MOCK_CASE" == job_failure ]]; then
      printf '2026-10-09T00:00:00Z,,1\n'
    else
      printf '2026-10-09T00:00:00Z,1,\n'
    fi
    ;;
  'logging read '*)
    count=$(cat "$LOG_COUNT_FILE")
    count=$((count + 1))
    printf '%s\n' "$count" > "$LOG_COUNT_FILE"
    [[ "$MOCK_CASE" != logging_failure ]] || exit 1
    [[ "$MOCK_CASE" != empty_logs ]] || exit 0
    if [[ "$MOCK_CASE" == delayed_logs && "$count" -eq 1 ]]; then
      exit 0
    fi
    if [[ "$MOCK_CASE" == changing_logs ]]; then
      printf 'line %s\n' "$count"
      exit 0
    fi
    if [[ "$MOCK_CASE" == partial_logs && "$count" -eq 1 ]]; then
      printf 'RECORDING_OPT_IN_BACKUP {"communities": {}, "event_details": {}}\n'
      exit 0
    fi
    printf 'RECORDING_OPT_IN_BACKUP {"communities": {}, "event_details": {}}\n変更はありません。\n'
    ;;
  *) exit 99 ;;
esac
EOF
chmod +x "$TMP_DIR/gcloud"
export PATH="$TMP_DIR:$PATH"
export LOG_RETRIES=3 LOG_RETRY_INTERVAL_SEC=0 WAIT_RETRIES=3 WAIT_INTERVAL_SEC=0
export CLOUDSDK_CONFIG="$TMP_DIR/gcloud-config" CLOUDSDK_ACTIVE_CONFIG_NAME='caller-config'
unset PROJECT_ID REGION JOB_NAME
TEST_COUNT=0

run_case() {
  export MOCK_CASE="$1"
  local expected="$2" status=0
  shift 2
  : > "$CALLS_FILE"
  printf '0\n' > "$LOG_COUNT_FILE"
  printf '0\n' > "$WAIT_COUNT_FILE"
  bash "$REPO_ROOT/scripts/run_manage_command.sh" "$@" > "$TMP_DIR/stdout" 2> "$TMP_DIR/stderr" || status=$?
  [[ "$status" -eq "$expected" ]] || fail "$MOCK_CASE: expected exit $expected, got $status"
  TEST_COUNT=$((TEST_COUNT + 1))
}

assert_restored() {
  assert_contains "$CALLS_FILE" '--args=^|^manage.py|migrate|--noinput'
  # EXIT で最後に呼ぶのは元の引数への復元。
  tail -n 6 "$CALLS_FILE" | grep -Fq -- '--args=^|^manage.py|migrate|--noinput' || fail 'Restore was not last'
}

run_case success 0 apply_recording_opt_in --keep-community-id '19' --defaults-before '2026-09-28 16:57:12,x' --dry-run
assert_contains "$CALLS_FILE" '--args=^|^manage.py|apply_recording_opt_in|--keep-community-id|19|--defaults-before|2026-09-28 16:57:12,x|--dry-run'
assert_contains "$CALLS_FILE" '--project=vrc-ta-hub'
assert_contains "$CALLS_FILE" '--region=asia-northeast1'
assert_contains "$CALLS_FILE" '--async'
assert_contains "$CALLS_FILE" '--format=value(metadata.name)'
assert_contains "$CALLS_FILE" 'run.googleapis.com/execution_name"="job-execution-this-call"'
assert_contains "$CALLS_FILE" '--order=asc'
assert_contains "$CALLS_FILE" "config=$CLOUDSDK_CONFIG active=caller-config"
assert_contains "$TMP_DIR/stdout" 'RECORDING_OPT_IN_BACKUP '
assert_contains "$TMP_DIR/stdout" '変更はありません。'
assert_restored

export PROJECT_ID=custom-project REGION=custom-region JOB_NAME=custom-job
run_case success 0 showmigrations --plan
assert_contains "$CALLS_FILE" 'custom-job'
assert_contains "$CALLS_FILE" '--project=custom-project'
assert_contains "$CALLS_FILE" '--region=custom-region'
assert_restored
unset PROJECT_ID REGION JOB_NAME

run_case success 2
[[ ! -s "$CALLS_FILE" ]] || fail 'No args should fail before gcloud'
run_case success 2 shell '--command=print("a|b")'
[[ ! -s "$CALLS_FILE" ]] || fail 'Delimiter should fail before gcloud'

for scenario in describe_failure empty_args; do
  run_case "$scenario" 2 showmigrations --plan
  if grep -Fq 'update' "$CALLS_FILE"; then
    fail "$scenario should not change args"
  fi
done

for scenario in update_failure execute_failure empty_execution never_completes empty_logs logging_failure changing_logs restore_failure; do
  run_case "$scenario" 2 showmigrations --plan
  assert_restored
done

run_case delayed_logs 0 showmigrations --plan
[[ "$(cat "$LOG_COUNT_FILE")" -eq 3 ]] || fail 'Logs should be retried until they are stable'
assert_contains "$TMP_DIR/stdout" 'RECORDING_OPT_IN_BACKUP '
assert_restored

# 取り込み途中（バックアップ行だけ）で終えず、出そろったログを出す。
run_case partial_logs 0 showmigrations --plan
assert_contains "$TMP_DIR/stdout" '変更はありません。'
assert_restored

run_case slow_execution 0 showmigrations --plan
[[ "$(cat "$WAIT_COUNT_FILE")" -eq 2 ]] || fail 'Execution should be polled until it completes'
assert_restored

# 実行が失敗しても、管理コマンドのエラー出力を表示してから失敗で終える。
run_case job_failure 2 showmigrations --plan
assert_contains "$TMP_DIR/stdout" '変更はありません。'
assert_contains "$TMP_DIR/stderr" 'job-execution-this-call'
assert_restored

for value in 'a[$(touch "$TMP_DIR/injected")]' '-1' '1e9'; do
  LOG_RETRIES="$value" run_case success 2 showmigrations --plan
  [[ ! -s "$CALLS_FILE" ]] || fail "Invalid LOG_RETRIES should fail before gcloud: $value"
done
[[ ! -e "$TMP_DIR/injected" ]] || fail 'LOG_RETRIES must not be evaluated'

printf 'PASS: run_manage_command.sh (%s cases)\n' "$TEST_COUNT"
