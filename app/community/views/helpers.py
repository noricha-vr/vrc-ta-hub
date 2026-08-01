"""community ビュー共通のヘルパー関数と定数."""
import logging

import requests
from django.conf import settings

from website.constants import build_site_url
from website.discord_webhook import (
    get_webhook_error_context,
    post_discord_webhook,
)

logger = logging.getLogger(__name__)

# 重複通報ブロック期間（秒）= 30日
REPORT_DUPLICATE_TTL_SECONDS = 30 * 24 * 60 * 60
# 同一IPからの月間通報上限（全集会合計）
REPORT_GLOBAL_LIMIT_PER_IP = 3


def _send_report_webhook(community, report_count):
    """活動停止通報の Discord Webhook を送信する."""
    webhook_url = settings.DISCORD_REPORT_WEBHOOK_URL
    if not webhook_url:
        return

    community_url = build_site_url(f"/community/{community.pk}/")
    message = {
        "content": (
            f"**集会の活動停止が通報されました**\n"
            f"\U0001f4e2 **{community.name}**\n"
            f"{community_url}\n\n"
            "活動しているかを確認して、リアクションで教えてください\n\n"
            "\u2705 \u2192 まだ開催されている\u3000\u274c \u2192 停止している\n\n"
            "\U0001f4ac 詳しい情報があればスレッドで教えてください"
        ),
        "embeds": [{
            "title": community.name,
            "url": community_url,
            "color": 16776960,
            "fields": [
                {"name": "通報数", "value": str(report_count), "inline": True},
            ],
        }],
    }

    try:
        post_discord_webhook(webhook_url, message)
        logger.info(
            f"通報Webhook送信成功: Community={community.name}"
        )
    except requests.RequestException as error:
        error_type, status_code = get_webhook_error_context(error)
        logger.error(
            "通報Webhook送信失敗: community_id=%s error_type=%s status_code=%s",
            community.pk,
            error_type,
            status_code,
        )
