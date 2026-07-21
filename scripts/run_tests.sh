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

# 実疎通は明示モードに分離し、通常の引数あり実行にもoffline境界を適用する。
if [ "${1:-}" = "--live-smoke" ]; then
    shift
    python3 "$(dirname "$0")/run_live_smoke.py" "$@"
elif [ $# -gt 0 ]; then
    docker compose exec vrc-ta-hub python -m tests.offline_manage test "$@" \
        --exclude-tag=live_smoke \
        --exclude-tag=e2e \
        --testrunner=website.tests.offline_runner.OfflineNetworkDiscoverRunner
else
    docker compose exec vrc-ta-hub python -m tests.offline_manage test "${TEST_APPS[@]}" \
        --exclude-tag=live_smoke \
        --exclude-tag=e2e \
        --testrunner=website.tests.offline_runner.OfflineNetworkDiscoverRunner \
        --verbosity=1
fi
