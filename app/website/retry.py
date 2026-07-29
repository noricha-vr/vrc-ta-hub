"""Webhook 等の外部 POST に共有のリトライ戦略を提供する.

`tenacity` を使い、非2xx応答から変換した HTTPError を含むすべての
requests.RequestException を最大3回試行し、試行間を1秒・2秒待機する。
4xx・429も一時的な制限や経路上の問題から回復できるよう意図的に再試行する。
最終的に失敗した場合は例外を呼び出し元へ再送出する。
"""

from __future__ import annotations

import logging

import requests
from tenacity import (
    RetryCallState,
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


def get_webhook_error_context(
    error: BaseException | None,
) -> tuple[str, int | None]:
    """Build a log-safe Webhook error type and HTTP status."""
    if error is None:
        return "UnknownError", None

    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    safe_status_code = status_code if type(status_code) is int else None
    return type(error).__name__, safe_status_code


def _log_retry_attempt(retry_state: RetryCallState) -> None:
    """tenacity の before_sleep フック: リトライ直前に warning ログを残す."""
    error = retry_state.outcome.exception() if retry_state.outcome else None
    error_type, status_code = get_webhook_error_context(error)
    logger.warning(
        "Webhook retry attempt=%s/%s error_type=%s status_code=%s",
        retry_state.attempt_number,
        WEBHOOK_RETRY_MAX_ATTEMPTS,
        error_type,
        status_code,
    )


def retry_webhook_post(func):
    """Discord Webhook 等の POST を最大3回試行するデコレータ.

    - 対象例外: すべての requests.RequestException（非2xxのHTTPErrorを含む）
    - 4xx・429も意図的にリトライ対象とする
    - 試行回数: 初回を含めて最大3回
    - リトライ間隔: 1秒、2秒
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
