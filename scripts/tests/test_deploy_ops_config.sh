#!/usr/bin/env bash
# 本番デプロイ運用の定義（Makefile の本番DBターゲット / migrate Job スクリプト）の静的検証。
# 実際に本番DB・GCP へ接続する検証は行わない。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

MAKEFILE="$REPO_ROOT/Makefile"
MIGRATE_JOB_SCRIPT="$REPO_ROOT/scripts/create_migrate_job.sh"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_file_contains() {
  local file="$1" pattern="$2"
  grep -Fq -- "$pattern" "$file" || fail "Expected '$pattern' in $(basename "$file")"
}

assert_file_not_contains() {
  local file="$1" pattern="$2"
  if grep -Fq -- "$pattern" "$file"; then
    fail "Did not expect '$pattern' in $(basename "$file")"
  fi
}

# Make の展開結果（実際に走るコマンド列）で検証する。
# 変数定義の見た目ではなく、ターゲット実行時に何が起きるかが振る舞い。
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

expand_target() {
  local target="$1"
  local out="$TMP_DIR/$target.expanded"

  if [[ ! -f "$out" ]]; then
    make -C "$REPO_ROOT" -n "$target" > "$out" 2>/dev/null \
      || fail "make -n $target failed"
  fi
  printf '%s\n' "$out"
}

assert_expansion_contains() {
  local target="$1" pattern="$2"
  grep -Fq -- "$pattern" "$(expand_target "$target")" \
    || fail "make -n $target should contain '$pattern'"
}

assert_expansion_not_contains() {
  local target="$1" pattern="$2"
  if grep -Fq -- "$pattern" "$(expand_target "$target")"; then
    fail "make -n $target should not contain '$pattern'"
  fi
}

# --- 課題1: 本番DBターゲットが 1Password 参照を解決し、Compose の db 経由で叩く ---

for target in db-backup db-pull db-push; do
  # op run 経由でなければ op:// 参照がホスト名として渡り接続に失敗する
  assert_expansion_contains "$target" "op run --env-file=.env.production.local --"
  # ホストの MariaDB クライアントは本番 MySQL 8 の認証プラグインに非対応
  assert_expansion_contains "$target" 'docker compose exec -T -e MYSQL_PWD db'
  # 値付き -e は docker CLI の argv に本番パスワードを平文で載せる（ps から読める）
  assert_expansion_not_contains "$target" '-e MYSQL_PWD="$DB_PASSWORD"'
  # op が無い環境では分かりやすく落とす
  assert_expansion_contains "$target" "command -v op"
done

# 本番DBを DROP する前に識別子検証とバックアップ健全性チェックを通す
assert_expansion_contains db-push "gunzip -t"
assert_expansion_contains db-push "must contain only letters, numbers, and underscores"
# 内側 sh に -e が無いと、バックアップ失敗後も DROP DATABASE が続行する
assert_expansion_contains db-push "sh -e -c"

# ローカルへ流し込むため GTID を落とす
assert_expansion_contains db-pull "--set-gtid-purged=OFF"

# 本番接続で SSL を無効化しない（RDS は SSL 接続を受け付ける）
assert_expansion_not_contains db-pull "--skip-ssl"
assert_expansion_not_contains db-backup "--skip-ssl"

# 生値の先読み（op:// がそのまま渡る原因）が残っていないこと
for var in DB_HOST DB_USER DB_PASSWORD DB_NAME; do
  assert_file_not_contains "$MAKEFILE" "grep '^${var}=' \"\$(PROD_ENV_FILE)\""
done

# 依存関係の維持
assert_file_contains "$MAKEFILE" "db-restore-production-local: db-backup-local db-pull"

# ローカル向けターゲットの挙動は変えない
assert_expansion_not_contains db-backup-local "op run"

# --- 課題2: migrate Job 作成スクリプト ---

[[ -x "$MIGRATE_JOB_SCRIPT" ]] || fail "create_migrate_job.sh must be executable"
bash -n "$MIGRATE_JOB_SCRIPT" || fail "create_migrate_job.sh has a syntax error"

# 稼働中サービスから引き継ぐ（ハードコードしない）
assert_file_contains "$MIGRATE_JOB_SCRIPT" "gcloud run services describe"
assert_file_contains "$MIGRATE_JOB_SCRIPT" "--service-account="
assert_file_contains "$MIGRATE_JOB_SCRIPT" "--set-secrets="

# 冪等（あれば update / なければ create）
assert_file_contains "$MIGRATE_JOB_SCRIPT" "gcloud run jobs describe"
assert_file_contains "$MIGRATE_JOB_SCRIPT" 'ACTION="update"'
assert_file_contains "$MIGRATE_JOB_SCRIPT" 'ACTION="create"'

# 既定のカンマ区切りだと manage.py migrate が壊れるため ^|^ を使う
assert_file_contains "$MIGRATE_JOB_SCRIPT" '^|^manage.py|migrate|--noinput'
# イメージの CMD は supervisord なので python を明示する
assert_file_contains "$MIGRATE_JOB_SCRIPT" "--command=python"

# migration の多重実行を防ぐ
assert_file_contains "$MIGRATE_JOB_SCRIPT" "--max-retries="
assert_file_contains "$MIGRATE_JOB_SCRIPT" 'TASK_TIMEOUT="10m"'

# 環境変数の値は一時ファイル経由で渡し、確実に削除する
# （trap の書き方には依存させない。ENV_VARS_FILE が EXIT trap で消えることだけを見る）
assert_file_contains "$MIGRATE_JOB_SCRIPT" "--env-vars-file="
if ! grep -E '^trap .*rm -f .*\$ENV_VARS_FILE.* EXIT$' "$MIGRATE_JOB_SCRIPT" >/dev/null; then
  printf 'FAIL: ENV_VARS_FILE must be removed by an EXIT trap in %s\n' "$MIGRATE_JOB_SCRIPT" >&2
  exit 1
fi
# 値を標準出力・ログに出さない
assert_file_not_contains "$MIGRATE_JOB_SCRIPT" "--set-env-vars"
assert_file_not_contains "$MIGRATE_JOB_SCRIPT" 'echo "$ENV_VARS_FILE"'

assert_file_contains "$MIGRATE_JOB_SCRIPT" 'REGION="${REGION:-asia-northeast1}"'
assert_file_contains "$MIGRATE_JOB_SCRIPT" 'PROJECT_ID="${PROJECT_ID:-vrc-ta-hub}"'

printf 'PASS: deploy ops config (Makefile / create_migrate_job.sh)\n'
