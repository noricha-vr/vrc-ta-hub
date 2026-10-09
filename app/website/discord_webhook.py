"""Discord Webhook 送信の共通ヘルパー.

複数アプリ（event / community）で同一実装が重複していたため、ここに一本化する。
"""

from __future__ import annotations

import requests
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator

from website.retry import get_webhook_error_context, retry_webhook_post

__all__ = [
    "DISCORD_WEBHOOK_URL_MESSAGE",
    "DISCORD_WEBHOOK_URL_REGEX",
    "discord_webhook_url_validator",
    "get_webhook_error_context",
    "is_discord_webhook_url",
    "post_discord_webhook",
]

# Discord の webhook URL として受け付ける形（discord.com のみ）。
# 集会の通知先（community）と告知の送信先（announcement）で同じ検証を使う
DISCORD_WEBHOOK_URL_REGEX = r"^https://discord\.com/api/webhooks/"
DISCORD_WEBHOOK_URL_MESSAGE = "Discord Webhook URL は https://discord.com/api/webhooks/ で始まる必要があります。"
discord_webhook_url_validator = RegexValidator(
    regex=DISCORD_WEBHOOK_URL_REGEX,
    message=DISCORD_WEBHOOK_URL_MESSAGE,
)

# Discord Webhook送信タイムアウト（秒）
DISCORD_TIMEOUT_SECONDS = 10


def is_discord_webhook_url(value: str) -> bool:
    """value が Discord の webhook URL の形か（discord_webhook_url_validator と同じ判定）。"""
    try:
        discord_webhook_url_validator(value)
    except ValidationError:
        return False
    return True


@retry_webhook_post
def post_discord_webhook(webhook_url: str, payload: dict) -> requests.Response:
    """Discord Webhook へ POST する内部ヘルパー（tenacity リトライ付き）.

    2xx 以外の HTTP 応答を例外化し、リトライ対象とする。
    最終的に失敗した場合は requests.RequestException 系を再送出する。
    """
    response = requests.post(
        webhook_url, json=payload, timeout=DISCORD_TIMEOUT_SECONDS
    )
    if not 200 <= response.status_code < 300:
        raise requests.HTTPError(
            f"Discord Webhook returned HTTP {response.status_code}",
            response=response,
        )
    return response
