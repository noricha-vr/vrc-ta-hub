#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_contains() {
  local pattern="$1"
  grep -Fq -- "$pattern" "$CALLS_FILE" || fail "Expected '$pattern'"
}

assert_not_contains() {
  local pattern="$1"
  if grep -Fq -- "$pattern" "$CALLS_FILE"; then
    fail "Did not expect '$pattern'"
  fi
}

cat > "$TMP_DIR/docker" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CALLS_FILE"
EOF
chmod +x "$TMP_DIR/docker"

export PATH="$TMP_DIR:$PATH"
export CALLS_FILE="$TMP_DIR/calls.log"

: > "$CALLS_FILE"
"$REPO_ROOT/scripts/run_tests.sh" twitter.tests.test_x_api
assert_contains "python -m tests.offline_manage test twitter.tests.test_x_api"
assert_contains "--exclude-tag=live_smoke"
assert_contains "--exclude-tag=e2e"
assert_contains "--testrunner=website.tests.offline_runner.OfflineNetworkDiscoverRunner"
assert_not_contains "python manage.py test twitter.tests.test_x_api"

: > "$CALLS_FILE"
"$REPO_ROOT/scripts/run_tests.sh" --live-smoke event.tests.test_google_calendar
assert_contains "python manage.py test --tag=live_smoke event.tests.test_google_calendar"
assert_not_contains "tests.offline_manage"
assert_not_contains "--exclude-tag=live_smoke"

printf 'PASS: run_tests.sh\n'
