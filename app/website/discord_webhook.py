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
