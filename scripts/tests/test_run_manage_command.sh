#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
export CALLS_FILE="$TMP_DIR/calls.log"
export LOG_COUNT_FILE="$TMP_DIR/log-count"
export WAIT_COUNT_FILE="$TMP_DIR/wait-count"
export ARGS_FILE="$TMP_DIR/args"
export JOB_ARGS_LOCK_DIR="$TMP_DIR/job-args.lock"

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
    if [[ "$MOCK_CASE" == busy_job ]]; then
      printf 'manage.py|other_command|--flag\n'
      exit 0
    fi
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
    for arg in "$@"; do
      [[ "$arg" != --args=* ]] || printf '%s\n' "${arg#--args=^|^}" > "$ARGS_FILE"
    done
    ;;
  'run jobs execute '*)
    [[ "$MOCK_CASE" != empty_execution ]] || exit 0
    # 実際の gcloud は --wait で失敗すると実行名を出さずに落ちる。--async だけが名前を返す。
    [[ "$*" == *--async* ]] || exit 1
    [[ "$MOCK_CASE" != execute_failure ]] || exit 1
    [[ "$MOCK_CASE" != slow_execute ]] || sleep 2
    printf 'job-execution-this-call\n'
    ;;
  'run jobs executions describe job-execution-this-call '*containers*)
    # 実行が使った引数。args_hijacked は、差し替えと execute の間に別の実行が引数を変えた場合。
    if [[ "$MOCK_CASE" == args_hijacked ]]; then
      printf 'manage.py|apply_recording_opt_in|--keep-community-id=19\n'
    elif [[ "$MOCK_CASE" == args_unknown ]]; then
      exit 0
    else
      cat "$ARGS_FILE"
    fi
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
    if [[ "$MOCK_CASE" == slow_ingest && "$count" -le 2 ]]; then
      printf 'RECORDING_OPT_IN_BACKUP {"communities": {}, "event_details": {}}\n'
      exit 0
    fi
    if [[ "$MOCK_CASE" == partial_logs && "$count" -eq 1 ]]; then
      printf 'RECORDING_OPT_IN_BACKUP {"communities": {}, "event_details": {}}\n'
      exit 0
    fi
    printf 'RECORDING_OPT_IN_BACKUP {"communities": {}, "event_details": {}}\n変更はありません。\nRECORDING_OPT_IN_DONE dry_run=True\n'
    ;;
  *) exit 99 ;;
esac
EOF
chmod +x "$TMP_DIR/gcloud"
export PATH="$TMP_DIR:$PATH"
export LOG_RETRIES=5 LOG_RETRY_INTERVAL_SEC=0 WAIT_RETRIES=3 WAIT_INTERVAL_SEC=0
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

# 途中のログが 2 回同じでも、完了の印が出るまで成功にしない。
EXPECT_LOG_PREFIX=RECORDING_OPT_IN_DONE run_case slow_ingest 0 apply_recording_opt_in --keep-community-id 19 --dry-run
assert_contains "$TMP_DIR/stdout" 'RECORDING_OPT_IN_DONE'
[[ "$(cat "$LOG_COUNT_FILE")" -eq 4 ]] || fail 'Should wait for the completion marker'
EXPECT_LOG_PREFIX=NO_SUCH_MARKER run_case success 2 showmigrations --plan
assert_contains "$TMP_DIR/stdout" '変更はありません。'
assert_contains "$TMP_DIR/stderr" 'NO_SUCH_MARKER'
assert_restored
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

for value in 'a[$(touch "$TMP_DIR/injected")]' '-1' '1e9' '08'; do
  LOG_RETRIES="$value" run_case success 2 showmigrations --plan
  [[ ! -s "$CALLS_FILE" ]] || fail "Invalid LOG_RETRIES should fail before gcloud: $value"
done
[[ ! -e "$TMP_DIR/injected" ]] || fail 'LOG_RETRIES must not be evaluated'

# ほかの実行が Job の引数を差し替えている最中は、何も変えずに断る。
run_case busy_job 2 apply_recording_opt_in --dry-run
grep -Fq 'jobs update' "$CALLS_FILE" && fail 'busy_job should not change args'
grep -Fq -- '--args=' "$CALLS_FILE" && fail 'busy_job should not change args'
assert_contains "$TMP_DIR/stderr" 'Another run may be in progress'

# 差し替えた後に別の実行が引数を変え、意図と違うコマンドが動いたら、ログを出してから失敗にする。
run_case args_hijacked 2 apply_recording_opt_in --keep-community-id=19 --dry-run
assert_contains "$TMP_DIR/stdout" '変更はありません。'
assert_contains "$TMP_DIR/stderr" 'not the requested'
assert_restored

# 実行が使った引数を確かめられない時も、成功にしない。
run_case args_unknown 2 showmigrations --plan
assert_contains "$TMP_DIR/stderr" '<unknown>'
assert_restored
[[ ! -e "$JOB_ARGS_LOCK_DIR" ]] || fail 'Lock should be released after a failure'

# 別の実行がロックを持っている間は、gcloud を呼ばずに断る。
mkdir -p "$JOB_ARGS_LOCK_DIR" && printf '%s\n' "$$" > "$JOB_ARGS_LOCK_DIR/pid"
run_case success 2 showmigrations --plan
[[ ! -s "$CALLS_FILE" ]] || fail 'A held lock should stop before gcloud'
assert_contains "$TMP_DIR/stderr" 'another run holds'
[[ "$(cat "$JOB_ARGS_LOCK_DIR/pid")" == "$$" ]] || fail 'Another run must not release a lock it does not hold'

# 持ち主が居ないロック（異常終了の残り）も自動では消さず、消し方を示して断る。
printf '999999\n' > "$JOB_ARGS_LOCK_DIR/pid"
run_case success 2 showmigrations --plan
[[ ! -s "$CALLS_FILE" ]] || fail 'A stale lock should stop before gcloud'
assert_contains "$TMP_DIR/stderr" 'stale lock'
[[ -e "$JOB_ARGS_LOCK_DIR" ]] || fail 'A stale lock must not be removed automatically'
rm -rf "$JOB_ARGS_LOCK_DIR"

run_case success 0 showmigrations --plan
[[ ! -e "$JOB_ARGS_LOCK_DIR" ]] || fail 'Lock should be released after success'

# 2 本を同時に動かすと、後から来た方は Job に触らずに断る。
export MOCK_CASE=slow_execute
: > "$CALLS_FILE"; printf '0\n' > "$LOG_COUNT_FILE"; printf '0\n' > "$WAIT_COUNT_FILE"
bash "$REPO_ROOT/scripts/run_manage_command.sh" showmigrations --plan > "$TMP_DIR/first.out" 2>&1 &
first=$!
for _ in $(seq 1 50); do [[ -e "$JOB_ARGS_LOCK_DIR/pid" ]] && break; sleep 0.1; done
second_status=0
bash "$REPO_ROOT/scripts/run_manage_command.sh" apply_recording_opt_in --dry-run > "$TMP_DIR/second.out" 2>&1 || second_status=$?
first_status=0
wait "$first" || first_status=$?
[[ "$second_status" -eq 2 ]] || fail "Concurrent run should be refused, got $second_status"
[[ "$first_status" -eq 0 ]] || fail "First run should succeed, got $first_status"
grep -Fq 'another run holds' "$TMP_DIR/second.out" || fail 'Second run should report the held lock'
grep -Fq 'apply_recording_opt_in' "$CALLS_FILE" && fail 'Second run must not change the job args'
TEST_COUNT=$((TEST_COUNT + 1))

# check_pending_migrations.sh も同じロックを使い、保持中は Job に触らない。
mkdir -p "$JOB_ARGS_LOCK_DIR" && printf '%s\n' "$$" > "$JOB_ARGS_LOCK_DIR/pid"
: > "$CALLS_FILE"
pending_status=0
MOCK_CASE=success bash "$REPO_ROOT/scripts/check_pending_migrations.sh" > "$TMP_DIR/pending.out" 2>&1 || pending_status=$?
[[ "$pending_status" -eq 2 ]] || fail "check_pending_migrations.sh should be refused, got $pending_status"
grep -Fq 'jobs update' "$CALLS_FILE" && fail 'check_pending_migrations.sh must not change the job args'
grep -Fq 'another run holds' "$TMP_DIR/pending.out" || fail 'check_pending_migrations.sh should report the held lock'
rm -rf "$JOB_ARGS_LOCK_DIR"
TEST_COUNT=$((TEST_COUNT + 1))

printf 'PASS: run_manage_command.sh (%s cases)\n' "$TEST_COUNT"
