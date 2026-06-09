"""Webhook 等の外部 POST に共有のリトライ戦略を提供する.

`tenacity` を使い、ネットワーク起因の一過性エラー（requests.RequestException /
requests.Timeout）に対して指数バックオフでリトライする。最終的に失敗した場合は
例外を呼び出し元に再送出し、既存の try/except による silent failure ハンドリング
を活かせる設計とする。
"""

from __future__ import annotations

import logging

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# Webhook POST の最大試行回数（初回 + リトライ2回 = 計3回）
WEBHOOK_RETRY_MAX_ATTEMPTS = 3
# 指数バックオフの最小待機秒（1s, 2s, 4s, ... の系列）
WEBHOOK_RETRY_WAIT_MIN_SECONDS = 1
# 指数バックオフの最大待機秒（過剰な待ち時間を防ぐ）
WEBHOOK_RETRY_WAIT_MAX_SECONDS = 10
# 指数バックオフの multiplier（wait_exponential の係数）
WEBHOOK_RETRY_WAIT_MULTIPLIER = 1


def _log_retry_attempt(retry_state) -> None:
    """tenacity の before_sleep フック: リトライ直前に warning ログを残す."""
    exception = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(
        "Webhook retry %s/%s after %s",
        retry_state.attempt_number,
        WEBHOOK_RETRY_MAX_ATTEMPTS,
        exception,
    )


def retry_webhook_post(func):
    """Discord Webhook 等の POST を 3 回まで指数バックオフでリトライするデコレータ.

    - 対象例外: requests.RequestException / requests.Timeout（一過性のネットワーク失敗）
    - リトライ間隔: 1s, 2s, 4s（exponential backoff, max 10s）
    - 最終失敗時は元の例外を再送出する（reraise=True）
    """
    return retry(
        stop=stop_after_attempt(WEBHOOK_RETRY_MAX_ATTEMPTS),
        wait=wait_exponential(
            multiplier=WEBHOOK_RETRY_WAIT_MULTIPLIER,
            min=WEBHOOK_RETRY_WAIT_MIN_SECONDS,
            max=WEBHOOK_RETRY_WAIT_MAX_SECONDS,
        ),
        retry=retry_if_exception_type((requests.RequestException, requests.Timeout)),
        reraise=True,
        before_sleep=_log_retry_attempt,
    )(func)
