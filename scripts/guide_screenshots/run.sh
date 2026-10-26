#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON="${GUIDE_PYTHON:-python3}"
PYTHON="$(command -v "$PYTHON")"
# 認証情報・通常 DB の環境変数を子プロセスへ引き継がない。
exec env -i HOME="$HOME" PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
  GUIDE_PORT="${GUIDE_PORT:-8019}" \
  SECRET_KEY=guide-demo-local-only DEBUG=True TESTING=0 DB_NAME=guide_demo \
  GOOGLE_API_KEY=DUMMY GOOGLE_CALENDAR_ID=demo@example.com GEMINI_API_KEY=DUMMY \
  OPENAI_API_KEY=DUMMY OPENROUTER_API_KEY=DUMMY REQUEST_TOKEN=DUMMY \
  X_API_KEY=DUMMY X_API_SECRET=DUMMY X_ACCESS_TOKEN=DUMMY X_ACCESS_TOKEN_SECRET=DUMMY \
  DISCORD_CLIENT_ID=guide-demo DISCORD_CLIENT_SECRET=DUMMY TURNSTILE_DEBUG_TEST_KEYS=false \
  ALLOWED_HOSTS=localhost,127.0.0.1 PYTHONUNBUFFERED=1 \
  "$PYTHON" "$ROOT/scripts/guide_screenshots/runner.py" "$@"
