"""community ビュー共通のヘルパー関数と定数."""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Discord Webhook 送信タイムアウト（秒）
DISCORD_REPORT_TIMEOUT_SECONDS = 10
# 重複通報ブロック期間（秒）= 30日
REPORT_DUPLICATE_TTL_SECONDS = 30 * 24 * 60 * 60
# 同一IPからの月間通報上限（全集会合計）
REPORT_GLOBAL_LIMIT_PER_IP = 3


def _send_report_webhook(community, report_count):
    """活動停止通報の Discord Webhook を送信する."""
    webhook_url = settings.DISCORD_REPORT_WEBHOOK_URL
    if not webhook_url:
        return

    community_url = f"https://vrc-ta-hub.com/community/{community.pk}/"
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
        response = requests.post(
            webhook_url, json=message, timeout=DISCORD_REPORT_TIMEOUT_SECONDS
        )
        if response.ok:
            logger.info(
                f"通報Webhook送信成功: Community={community.name}"
            )
        else:
            logger.warning(
                f"通報Webhook送信失敗: status={response.status_code}"
            )
    except requests.RequestException:
        logger.exception("通報Webhook送信で例外が発生")
