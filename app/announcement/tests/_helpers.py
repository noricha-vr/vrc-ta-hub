"""announcement のテスト共通のヘルパー。"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from announcement.models import DiscordScheduledMessage

JST = ZoneInfo('Asia/Tokyo')
# テスト用の偽の webhook。トークン部分がログや画面に漏れていないかを、この文字列で確かめる
FAKE_WEBHOOK_TOKEN = 'fake-webhook-token-for-tests'
FAKE_WEBHOOK_URL = f'https://discord.com/api/webhooks/123456789/{FAKE_WEBHOOK_TOKEN}'
FAKE_DISCORD_MESSAGE_ID = '1300000000000000001'


def jst(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=JST)


def make_message(**fields) -> DiscordScheduledMessage:
    defaults = {
        'body': 'テストの告知です',
        'scheduled_at': jst(2026, 10, 1, 20, 0),
        'status': DiscordScheduledMessage.Status.SCHEDULED,
    }
    defaults.update(fields)
    return DiscordScheduledMessage.objects.create(**defaults)


def discord_response(status_code: int = 200, payload: dict | None = None) -> MagicMock:
    """requests.post が返す Discord の応答の代わり。"""
    response = MagicMock()
    response.status_code = status_code
    if payload is None and 200 <= status_code < 300:
        payload = {'id': FAKE_DISCORD_MESSAGE_ID, 'content': 'テストの告知です'}
    if payload is None:
        response.json.side_effect = ValueError('no json')
    else:
        response.json.return_value = payload
    return response
