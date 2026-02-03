#!/bin/bash
# テスト実行スクリプト
# Python 3.12のモジュールディスカバリ問題を回避するため、
# 明示的にテストパスを指定して実行する

TEST_APPS=(
    "api_v1.tests"
    "community.tests"
    "event.tests"
    "event_calendar.tests"
    "guide.tests"
    "ta_hub.tests"
    "twitter.tests"
    "user_account.tests"
    "website.tests"
)

# 引数があればそれを使う、なければ全アプリ
if [ $# -gt 0 ]; then
    docker compose exec vrc-ta-hub python manage.py test "$@"
else
    docker compose exec vrc-ta-hub python manage.py test "${TEST_APPS[@]}" --verbosity=1
fi
