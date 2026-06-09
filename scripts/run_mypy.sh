#!/bin/bash
# mypy を warn-only で実行する補助スクリプト。
#
# 目的:
# - 段階導入中の mypy エラー件数をローカル/CI で簡単に観測できるようにする。
# - CI 連携時にそのまま流用できる (exit code 0 を強制するため、警告で job を落とさない)。
#
# 使い方:
#   # ホスト (uv 環境) で実行
#   bash scripts/run_mypy.sh
#
#   # Docker コンテナ内で実行 (Django 設定が解決済みで安定)
#   docker exec vrc-ta-hub-app bash -c "cd /app && mypy event/services/ --config-file /pyproject.toml"
#
# 仕様:
# - 対象は app/event/services/ のみ (段階導入の第一弾)。
# - 設定は pyproject.toml の [tool.mypy] / [[tool.mypy.overrides]] を参照する。
# - django-stubs プラグインが Django 設定を初期化するため、DB_NAME 等の環境変数が
#   未設定だと起動エラーになる。CI ではダミー値を export してから呼ぶ運用にする。
# - 詳細は docs/mypy.md を参照。
set -e
cd "$(dirname "$0")/.."

echo "=== mypy: app/event/services/ ==="
mypy app/event/services/ || true  # warn-only: 警告で失敗させない

echo ""
echo "=== mypy エラー件数 ==="
# `grep -c` がマッチ 0 のとき exit 1 を返すため `|| true` で握り潰す
mypy app/event/services/ 2>&1 | grep -c "error:" || true
