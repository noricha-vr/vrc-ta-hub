"""集会への Discord 通知の送り先選択と Discord Bot の REST 送信。"""

from dataclasses import dataclass, field
import re
from typing import Literal

import requests
from django.conf import settings

from website.discord_webhook import DISCORD_TIMEOUT_SECONDS
from website.retry import retry_webhook_post


@dataclass(frozen=True)
class DiscordDelivery:
    kind: Literal['bot', 'webhook', 'none']
    webhook_url: str = field(default='', repr=False)
    channel_id: str = field(default='', repr=False)


def _valid_channel_id(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r'[0-9]{17,20}', value) is not None


def select_community_discord_delivery(community) -> DiscordDelivery:
    """明示的に許可された、設定済みの集会だけを Bot 経路にする。"""
    webhook_url = community.notification_webhook_url or ''
    allowed_ids = {
        value.strip() for value in settings.DISCORD_BOT_DELIVERY_COMMUNITY_IDS.split(',') if value.strip()
    }
    if str(community.pk) in allowed_ids and settings.DISCORD_BOT_TOKEN:
        # 同じ集会に記録が複数ある時は、最後に作られた configured の記録の通知先だけを使う。
        installation = community.bot_installations.filter(status='configured').order_by('-pk').first()
        if installation and _valid_channel_id(installation.notification_channel_id):
            return DiscordDelivery('bot', webhook_url, installation.notification_channel_id)
    return DiscordDelivery('webhook' if webhook_url else 'none', webhook_url)


@retry_webhook_post(excluded_status_codes=(403, 404))
def post_discord_bot(channel_id: str, payload: dict) -> requests.Response:
    """Webhook と同じ再試行を使う。403/404 は呼び出し元で退避する。"""
    if not _valid_channel_id(channel_id):
        raise ValueError('Invalid Discord Bot notification channel')
    if not settings.DISCORD_BOT_TOKEN:
        raise ValueError('Discord Bot token is not configured')
    # Webhook 固有の username/avatar_url/thread_name 等を Bot API に渡さない。
    bot_payload = {key: value for key, value in payload.items() if key in {'content', 'embeds', 'allowed_mentions'}}
    try:
        response = requests.post(
            f'https://discord.com/api/v10/channels/{channel_id}/messages',
            headers={'Authorization': f'Bot {settings.DISCORD_BOT_TOKEN}'},
            json=bot_payload,
            timeout=DISCORD_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
    except requests.RequestException:
        # requests の例外本文には送信 URL 等が含まれ得るため、そのまま外へ出さない。
        raise requests.RequestException('Discord Bot request failed') from None
    if not 200 <= response.status_code < 300:
        raise requests.HTTPError(f'Discord Bot returned HTTP {response.status_code}', response=response)
    return response
