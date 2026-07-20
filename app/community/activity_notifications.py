"""集会活動監視のDiscord通知。"""

import logging
from typing import Any, Literal

import requests
from django.conf import settings

from website.constants import build_site_url
from website.retry import retry_webhook_post

from .activity_client import ActivityAssessment
from .models import Community

logger = logging.getLogger(__name__)
DISCORD_TIMEOUT_SECONDS = 10


@retry_webhook_post
def _post_discord_webhook(webhook_url: str, payload: dict[str, Any]) -> requests.Response:
    response = requests.post(
        webhook_url,
        json=payload,
        timeout=DISCORD_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response


def send_activity_notification(
    community: Community,
    *,
    assessment: ActivityAssessment,
    notification_type: Literal["warning", "hidden"],
    consecutive_checks: int,
) -> bool:
    """管理者向けDiscordへ警告/非表示結果を通知する。"""
    webhook_url = (
        settings.COMMUNITY_ACTIVITY_DISCORD_WEBHOOK_URL
        or settings.DISCORD_REPORT_WEBHOOK_URL
        or settings.DISCORD_WEBHOOK_URL
    )
    if not webhook_url:
        logger.warning(
            "活動監視Webhookが未設定のため通知と自動非表示を保留: community_id=%s",
            community.pk,
        )
        return False

    if notification_type == "warning":
        title = "⚠️ 活動停止の可能性を検知"
        content = "自動非表示前の確認対象です。誤判定の場合は管理画面で監視をOFFにしてください。"
        color = 16753920
    else:
        title = "🗄️ 集会を自動非表示にしました"
        content = "連続判定と猶予期間を満たしたため、公開一覧からアーカイブへ移動しました。"
        color = 9807270

    evidence_lines = []
    for item in assessment.evidence[:5]:
        label = item.get("posted_at") or "X投稿"
        summary = str(item.get("summary") or "根拠")[:180]
        evidence_lines.append(f"[{label}: {summary}]({item['url']})")
    evidence_text = "\n".join(evidence_lines) if evidence_lines else "検証可能な投稿URLなし（期間内活動なし等の判定）"
    community_url = build_site_url(f"/community/{community.pk}/")
    fields = [
        {"name": "判定", "value": assessment.decision, "inline": True},
        {"name": "信頼度", "value": f"{assessment.confidence:.1%}", "inline": True},
        {"name": "連続判定", "value": f"{consecutive_checks}回", "inline": True},
        {
            "name": "最終活動日",
            "value": assessment.last_activity_at.isoformat() if assessment.last_activity_at else "確認できず",
            "inline": True,
        },
        {"name": "シグナル", "value": assessment.signal or "-", "inline": True},
        {"name": "理由", "value": assessment.reason[:1000], "inline": False},
        {"name": "根拠", "value": evidence_text[:1000], "inline": False},
    ]
    payload = {
        "content": f"{content}\n{community_url}",
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": title,
                "description": community.name[:500],
                "url": community_url,
                "color": color,
                "fields": fields,
                "footer": {"text": "Grok + X Searchによる補助判定。削除ではなくend_atで非表示化します。"},
            }
        ],
    }
    try:
        _post_discord_webhook(webhook_url, payload)
        return True
    except requests.RequestException:
        logger.exception("活動監視Discord通知に失敗: community_id=%s", community.pk)
        return False
