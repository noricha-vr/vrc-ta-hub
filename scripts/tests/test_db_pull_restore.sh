#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

source "$REPO_ROOT/scripts/db_pull_restore.sh"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_contains() {
  local file="$1"
  local pattern="$2"

  grep -Fq "$pattern" "$file" || fail "Expected '$pattern' in $file"
}

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

DUMP_PATH="$TMP_DIR/production.sql.gz"
printf 'SELECT 1;\n' | gzip > "$DUMP_PATH"

run_with_mock_compose() {
  local calls_file="$1"
  MOCK_APP_DB_HOST="$2"
  MOCK_VERIFY_COUNT_OUTPUT="$3"
  shift 3

  compose() {
    printf '%s\n' "$*" >> "$calls_file"

    if [[ "$1" == "exec" && "$2" == "-T" && "$4" == "env" ]]; then
      case "$3" in
        "$APP_SERVICE")
          printf 'DB_NAME=local_vrc_ta_hub\nDB_HOST=%s\nDB_USER=root\nDB_PASSWORD=root\n' "$MOCK_APP_DB_HOST"
          ;;
        *)
          return 1
          ;;
      esac
      return 0
    fi

    if [[ "$1" == "exec" && "$2" == "-T" && "$3" == "-e" && "$5" == "$DB_SERVICE" && "$6" == "mysql" ]]; then
      local mysql_has_execute=0
      local after_service=0
      local arg

      for arg in "$@"; do
        if (( after_service )) && [[ "$arg" == "-e" ]]; then
          mysql_has_execute=1
        fi
        if [[ "$arg" == "$DB_SERVICE" ]]; then
          after_service=1
        fi
      done

      if (( ! mysql_has_execute )); then
        cat >/dev/null
      fi
      return 0
    fi

    if [[ "$1" == "exec" && "$2" == "-T" && "$3" == "$APP_SERVICE" && "$4" == "python" ]]; then
      printf '%s\n' "$MOCK_VERIFY_COUNT_OUTPUT"
      return 0
    fi

    return 1
  }

  "$@"
}

success_calls="$TMP_DIR/success.calls"
run_with_mock_compose "$success_calls" "db" $'settings log\n3' main "$DUMP_PATH" > "$TMP_DIR/success.out"
assert_contains "$TMP_DIR/success.out" "Verified via app container: local_vrc_ta_hub.vket_collaboration has 3 rows."
assert_contains "$success_calls" "exec -T -e MYSQL_PWD=root db mysql"
assert_contains "$success_calls" "exec -T vrc-ta-hub python manage.py shell -c"

host_mismatch_calls="$TMP_DIR/host-mismatch.calls"
if ( run_with_mock_compose "$host_mismatch_calls" "127.0.0.1" "3" main "$DUMP_PATH" > "$TMP_DIR/host-mismatch.out" 2> "$TMP_DIR/host-mismatch.err" ); then
  fail "DB_HOST mismatch should fail"
fi
assert_contains "$TMP_DIR/host-mismatch.err" "App DB_HOST is '127.0.0.1'"

zero_count_calls="$TMP_DIR/zero-count.calls"
if ( run_with_mock_compose "$zero_count_calls" "db" "0" main "$DUMP_PATH" > "$TMP_DIR/zero-count.out" 2> "$TMP_DIR/zero-count.err" ); then
  fail "Zero representative rows should fail"
fi
assert_contains "$TMP_DIR/zero-count.err" "expected at least 1"

printf 'PASS: db_pull_restore.sh\n'
