#!/bin/bash
# テスト実行スクリプト
# CI (.github/workflows/ci.yml) と同じ全探索で実行し、実行範囲のずれを防ぐ

# 実疎通は明示モードに分離し、通常の引数あり実行にもoffline境界を適用する。
if [ "${1:-}" = "--live-smoke" ]; then
    shift
    python3 "$(dirname "$0")/run_live_smoke.py" "$@"
elif [ $# -gt 0 ]; then
    docker compose exec vrc-ta-hub python -m tests.offline_manage test "$@" \
        --exclude-tag=live_smoke \
        --exclude-tag=e2e \
        --testrunner=website.tests.offline_runner.OfflineNetworkDiscoverRunner \
        --noinput
else
    docker compose exec vrc-ta-hub python -m tests.offline_manage test \
        --exclude-tag=live_smoke \
        --exclude-tag=e2e \
        --testrunner=website.tests.offline_runner.OfflineNetworkDiscoverRunner \
        --noinput \
        --verbosity=1
fi
