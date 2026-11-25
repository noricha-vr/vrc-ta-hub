#!/bin/sh
set -eu

# 共有 R2 バケットへこのリビジョンのハッシュ付き JS/CSS を冪等同期してから Web を起動する。
# 同期失敗時は Cloud Run のヘルスチェックを通さず fail closed で停止する。
python3 /app/manage.py sync_container_static

exec supervisord -c /etc/supervisor/supervisord.conf -n
