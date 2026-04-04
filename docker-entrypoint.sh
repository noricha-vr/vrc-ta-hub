#!/bin/sh
set -eu

# Cloud Run deploy は新リビジョンを作るだけで Django migration を自動適用しない。
# schema が遅れると今回の tweet_queue のように実テーブル不在で 500 になるため、
# アプリ起動前に migrate を終わらせてから web プロセスを立ち上げる。参照: PR #174（デプロイ時の migration 自動適用）
python manage.py migrate_with_lock --noinput

exec supervisord -c /etc/supervisor/supervisord.conf -n
