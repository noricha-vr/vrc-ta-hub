"""Discord Webhook 送信の共通ヘルパー.

複数アプリ（event / community）で同一実装が重複していたため、ここに一本化する。
"""

from __future__ import annotations

import requests

from website.retry import retry_webhook_post

# Discord Webhook送信タイムアウト（秒）
DISCORD_TIMEOUT_SECONDS = 10


@retry_webhook_post
def post_discord_webhook(webhook_url: str, payload: dict) -> requests.Response:
    """Discord Webhook へ POST する内部ヘルパー（tenacity リトライ付き）.

    HTTP エラー (4xx/5xx) も raise_for_status で例外化し、リトライ対象とする。
    最終的に失敗した場合は requests.RequestException 系を再送出する。
    """
    response = requests.post(
        webhook_url, json=payload, timeout=DISCORD_TIMEOUT_SECONDS
    )
    response.raise_for_status()
    return response
