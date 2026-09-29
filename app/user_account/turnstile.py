"""ログイン画面の Cloudflare Turnstile（ボット対策）の有効判定とサーバー側検証.

TURNSTILE_SITE_KEY と TURNSTILE_SECRET_KEY が 2 つとも設定されている時だけ有効にする。
フォームから届いたトークンは信用せず、毎回 Cloudflare の siteverify に問い合わせて判定する。
"""
from __future__ import annotations

import logging
import uuid
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
# Cloudflare の内部エラーの時だけ 1 回再試行する（初回 + 再試行 1 回）
SITEVERIFY_MAX_ATTEMPTS = 2
HTTP_OK = 200
HTTP_SERVER_ERROR_MIN_STATUS = 500
# Cloudflare 側の内部エラー（再試行すれば通りうる）
CLOUDFLARE_INTERNAL_ERROR_CODES = frozenset({'internal-error'})
# こちらのシークレットキーの設定ミス。利用者の送信内容では起きない
SECRET_MISCONFIGURED_ERROR_CODES = frozenset({'missing-input-secret', 'invalid-input-secret'})


class TurnstileResult(Enum):
    """siteverify の判定結果."""

    PASSED = 'passed'
    FAILED = 'failed'
    # Cloudflare 側の障害で、トークンの正否を判定できなかった
    UNAVAILABLE = 'unavailable'


def is_turnstile_enabled() -> bool:
    """サイトキーとシークレットキーが 2 つとも設定されている時だけ True を返す."""
    return bool(settings.TURNSTILE_SITE_KEY and settings.TURNSTILE_SECRET_KEY)


def verify_turnstile_token(token: str, remote_ip: str) -> TurnstileResult:
    """ウィジェットが発行したトークンを siteverify で検証する.

    トークンが無い・長すぎる時は Cloudflare に問い合わせずに FAILED を返す。
    シークレットキーの設定ミスも FAILED（ログインは拒否し、error ログで知らせる）。
    Cloudflare 側の障害（接続失敗・タイムアウト・5xx・JSON でない応答・再試行しても続く internal-error）は、
    理由をログに残して UNAVAILABLE を返す。UNAVAILABLE をどう扱うか（ログインを通すか）は呼び出し側が決める。
    """
    if not token or len(token) > MAX_TOKEN_LENGTH:
        return TurnstileResult.FAILED
    # 再試行でトークンの二重使用（timeout-or-duplicate）と判定されないよう、全試行で同じ idempotency_key を送る
    payload = {
        'secret': settings.TURNSTILE_SECRET_KEY,
        'response': token,
        'remoteip': remote_ip,
        'idempotency_key': str(uuid.uuid4()),
    }
    for attempt in range(1, SITEVERIFY_MAX_ATTEMPTS + 1):
        outcome = _post_siteverify(payload)
        if outcome is None:
            return TurnstileResult.UNAVAILABLE
        result = _judge_outcome(*outcome)
        if result is not None:
            return result
        logger.warning(
            'Turnstile siteverify reported an internal error: attempt=%s/%s',
            attempt,
            SITEVERIFY_MAX_ATTEMPTS,
        )
    return TurnstileResult.UNAVAILABLE


def _post_siteverify(payload: dict[str, str]) -> tuple[int, dict[str, Any]] | None:
    """siteverify に問い合わせて (HTTP ステータス, 応答の JSON) を返す。判定に使えない応答なら None を返す."""
    try:
        # 転送先に secret を送らないよう、リダイレクトはたどらない
        response = requests.post(
            SITEVERIFY_URL,
            data=payload,
            timeout=SITEVERIFY_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        logger.warning(
            'Turnstile siteverify is unavailable: reason=request_error exception_type=%s',
            type(exc).__name__,
        )
        return None
    if 300 <= response.status_code < 400:
        # 転送はたどらない。3xx は障害ではなく想定外の応答なので、fail-open に流さず検証失敗にする
        logger.warning('Turnstile siteverify returned a redirect: status=%s', response.status_code)
        return response.status_code, {}
    if response.status_code >= HTTP_SERVER_ERROR_MIN_STATUS:
        logger.warning(
            'Turnstile siteverify is unavailable: reason=server_error status=%s',
            response.status_code,
        )
        return None
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        logger.warning(
            'Turnstile siteverify is unavailable: reason=unexpected_body status=%s',
            response.status_code,
        )
        return None
    return response.status_code, body


def _judge_outcome(status_code: int, body: dict[str, Any]) -> TurnstileResult | None:
    """siteverify の応答を判定する。Cloudflare の内部エラー（再試行してよい）なら None を返す.

    トークンと secret はログに出さない。
    """
    if status_code == HTTP_OK and body.get('success') is True:
        return TurnstileResult.PASSED
    raw_codes = body.get('error-codes')
    error_codes = sorted({str(code) for code in raw_codes}) if isinstance(raw_codes, list) else []
    # 鍵の設定ミスは内部エラーより先に見る。両方が併記されても、再試行→fail-open に流さず拒否する
    if SECRET_MISCONFIGURED_ERROR_CODES.intersection(error_codes):
        # 人が直すまで誰も通れないので error で出す（Sentry に上がる）。ボット対策を黙って外さないよう拒否する
        logger.error('Turnstile secret key is misconfigured: error_codes=%s', error_codes)
        return TurnstileResult.FAILED
    if CLOUDFLARE_INTERNAL_ERROR_CODES.intersection(error_codes):
        return None
    logger.info('Turnstile verification failed: status=%s error_codes=%s', status_code, error_codes)
    return TurnstileResult.FAILED
