#!/bin/bash
# 定期イベントの生成と同期を実行するスクリプト

# スクリプトのディレクトリを取得
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$SCRIPT_DIR/../.."

# 環境変数を読み込み
export $(cat $PROJECT_ROOT/.env.local | xargs)

# Dockerコンテナで実行
cd $PROJECT_ROOT

echo "$(date): 定期イベント生成開始"
docker compose exec -T vrc-ta-hub python manage.py generate_recurring_events

echo "$(date): Googleカレンダー同期開始"
docker compose exec -T vrc-ta-hub python manage.py sync_calendar

echo "$(date): 処理完了"