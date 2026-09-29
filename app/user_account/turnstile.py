"""ログイン画面の Cloudflare Turnstile（ボット対策）の有効判定とサーバー側検証.

TURNSTILE_SITE_KEY と TURNSTILE_SECRET_KEY が 2 つとも設定されている時だけ有効にする。
フォームから届いたトークンは信用せず、毎回 Cloudflare の siteverify に問い合わせて判定する。
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

SITEVERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'
# ウィジェットがフォームに足す hidden input の name（Cloudflare の既定値）
RESPONSE_FIELD_NAME = 'cf-turnstile-response'
# Cloudflare の仕様上のトークン長の上限。超える値は問い合わせずに不正とする
MAX_TOKEN_LENGTH = 2048
SITEVERIFY_TIMEOUT_SECONDS = 5
HTTP_SERVER_ERROR_MIN_STATUS = 500
# Cloudflare 側の内部エラー（再試行すれば通りうる）
CLOUDFLARE_INTERNAL_ERROR_CODES = frozenset({'internal-error'})
# こちらのシークレットキーの設定ミス。利用者の送信内容では起きない
SECRET_MISCONFIGURED_ERROR_CODES = frozenset({'missing-input-secret', 'invalid-input-secret'})


class TurnstileResult(Enum):
    """siteverify の判定結果."""

    PASSED = 'passed'
    FAILED = 'failed'
    # Cloudflare 側の障害や設定ミスで、トークンの正否を判定できなかった
    UNAVAILABLE = 'unavailable'


def is_turnstile_enabled() -> bool:
    """サイトキーとシークレットキーが 2 つとも設定されている時だけ True を返す."""
    return bool(settings.TURNSTILE_SITE_KEY and settings.TURNSTILE_SECRET_KEY)


def verify_turnstile_token(token: str, remote_ip: str) -> TurnstileResult:
    """ウィジェットが発行したトークンを siteverify で検証する.

    トークンが無い・長すぎる時は Cloudflare に問い合わせずに FAILED を返す。
    判定できない時（接続失敗・タイムアウト・5xx・応答の形式不正・Cloudflare の内部エラー・
    シークレットキーの設定ミス）は、理由をログに残して UNAVAILABLE を返す。
    UNAVAILABLE をどう扱うか（ログインを通すか）は呼び出し側が決める。
    """
    if not token or len(token) > MAX_TOKEN_LENGTH:
        return TurnstileResult.FAILED
    outcome = _post_siteverify(token, remote_ip)
    if outcome is None:
        return TurnstileResult.UNAVAILABLE
    return _classify_outcome(outcome)


def _post_siteverify(token: str, remote_ip: str) -> dict[str, Any] | None:
    """siteverify に問い合わせて応答の JSON を返す。判定に使えない応答なら None を返す."""
    payload = {
        'secret': settings.TURNSTILE_SECRET_KEY,
        'response': token,
        'remoteip': remote_ip,
    }
    try:
        response = requests.post(SITEVERIFY_URL, data=payload, timeout=SITEVERIFY_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        logger.warning(
            'Turnstile siteverify is unavailable: reason=request_error exception_type=%s',
            type(exc).__name__,
        )
        return None
    if response.status_code >= HTTP_SERVER_ERROR_MIN_STATUS:
        logger.warning(
            'Turnstile siteverify is unavailable: reason=server_error status=%s',
            response.status_code,
        )
        return None
    try:
        outcome = response.json()
    except ValueError:
        outcome = None
    if not isinstance(outcome, dict):
        logger.warning(
            'Turnstile siteverify is unavailable: reason=unexpected_body status=%s',
            response.status_code,
        )
        return None
    return outcome


def _classify_outcome(outcome: dict[str, Any]) -> TurnstileResult:
    """siteverify の応答を判定結果に変換する。トークンと secret はログに出さない."""
    if outcome.get('success') is True:
        return TurnstileResult.PASSED
    raw_codes = outcome.get('error-codes')
    error_codes = sorted({str(code) for code in raw_codes}) if isinstance(raw_codes, list) else []
    if SECRET_MISCONFIGURED_ERROR_CODES.intersection(error_codes):
        # 人の対応が要るので error で出す（Sentry に上がる）
        logger.error('Turnstile secret key is misconfigured: error_codes=%s', error_codes)
        return TurnstileResult.UNAVAILABLE
    if CLOUDFLARE_INTERNAL_ERROR_CODES.intersection(error_codes):
        logger.warning('Turnstile siteverify is unavailable: reason=internal_error')
        return TurnstileResult.UNAVAILABLE
    logger.info('Turnstile verification failed: error_codes=%s', error_codes)
    return TurnstileResult.FAILED
