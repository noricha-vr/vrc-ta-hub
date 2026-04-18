"""X 投稿関連の通知

投稿失敗を Admin Discord Webhook に通知し、管理者が即時に検知できるようにする。
"""
import logging

import requests
from django.conf import settings

from twitter.x_api import PostTweetResult

logger = logging.getLogger(__name__)

DISCORD_TIMEOUT_SECONDS = 10
DISCORD_FIELD_MAX_LENGTH = 1024
DISCORD_DESC_MAX_LENGTH = 4000
COLOR_RED = 15548997


def notify_tweet_post_failure(queue_item, result: PostTweetResult) -> None:
    """X API 投稿失敗時に Admin Discord Webhook へ通知する。

    ツイート本文を description に含めて送る。
    settings.DISCORD_WEBHOOK_URL が未設定の場合は何もしない。
    """
    webhook_url = getattr(settings, "DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        return

    tweet_text = queue_item.generated_text or "(本文なし)"
    if len(tweet_text) > DISCORD_DESC_MAX_LENGTH - 10:
        tweet_text = tweet_text[: DISCORD_DESC_MAX_LENGTH - 13] + "..."

    error_body = result.get("error_body") or "(なし)"
    if len(error_body) > DISCORD_FIELD_MAX_LENGTH:
        error_body = error_body[: DISCORD_FIELD_MAX_LENGTH - 3] + "..."

    status_code = result.get("status_code")
    status_code_text = str(status_code) if status_code is not None else "N/A"

    detail_url = f"https://vrc-ta-hub.com/twitter/queue/{queue_item.pk}/"

    fields = [
        {"name": "集会", "value": queue_item.community.name, "inline": True},
        {"name": "種別", "value": queue_item.get_tweet_type_display(), "inline": True},
        {"name": "HTTPステータス", "value": status_code_text, "inline": True},
        {"name": "エラー内容", "value": error_body, "inline": False},
        {"name": "キュー詳細", "value": detail_url, "inline": False},
    ]

    message = {
        "content": f"❌ **X投稿に失敗しました** (queue #{queue_item.pk})",
        "embeds": [{
            "title": f"投稿失敗: {queue_item.get_tweet_type_display()}",
            "description": f"```\n{tweet_text}\n```",
            "color": COLOR_RED,
            "fields": fields,
            "footer": {"text": f"queue #{queue_item.pk}"},
        }],
    }

    try:
        response = requests.post(
            webhook_url, json=message, timeout=DISCORD_TIMEOUT_SECONDS,
        )
        if response.ok:
            logger.info(
                "Admin Webhook通知成功（投稿失敗）: queue #%s", queue_item.pk,
            )
        else:
            logger.warning(
                "Admin Webhook通知失敗（投稿失敗）: queue #%s, status=%s",
                queue_item.pk,
                response.status_code,
            )
    except Exception:
        logger.exception(
            "Admin Webhook通知エラー（投稿失敗）: queue #%s", queue_item.pk,
        )
