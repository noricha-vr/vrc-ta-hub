"""LT申請の通知サービス"""
import logging

import requests
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse

from event.models import EventDetail

logger = logging.getLogger(__name__)

# Discord Webhook送信タイムアウト（秒）
DISCORD_TIMEOUT_SECONDS = 10


def notify_owners_of_new_application(event_detail: EventDetail, request=None) -> None:
    """
    新しいLT申請を主催者に通知する

    Args:
        event_detail: 申請されたEventDetailインスタンス
        request: HTTPリクエスト（絶対URL生成用）
    """
    community = event_detail.event.community
    owners = community.get_owners()

    if not owners:
        logger.warning(
            f"LT申請通知: 主催者が見つかりません。Community={community.name}"
        )
        return

    # レビューページのURL
    review_path = reverse('event:lt_application_review', kwargs={'pk': event_detail.pk})
    if request:
        review_url = request.build_absolute_uri(review_path)
    else:
        review_url = f"https://vrc-ta-hub.com{review_path}"

    # メール送信
    for owner in owners:
        if not owner.email:
            logger.warning(
                f"LT申請通知: メールアドレスがありません。User={owner.user_name}"
            )
            continue

        context = {
            'owner': owner,
            'community': community,
            'event_detail': event_detail,
            'event': event_detail.event,
            'review_url': review_url,
        }

        subject = f"[{community.name}] 新しいLT申請があります"
        html_message = render_to_string(
            'event/email/lt_application_received.html', context
        )

        try:
            sent = send_mail(
                subject=subject,
                message='',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[owner.email],
                html_message=html_message,
            )
            if sent:
                logger.info(
                    f"LT申請通知メール送信成功: {owner.email} (申請ID={event_detail.pk})"
                )
            else:
                logger.warning(
                    f"LT申請通知メール送信失敗: {owner.email} (申請ID={event_detail.pk})"
                )
        except Exception as e:
            logger.error(
                f"LT申請通知メール送信エラー: {owner.email} (申請ID={event_detail.pk}): {e}"
            )

    # Discord Webhook通知
    _send_discord_notification_for_new_application(event_detail, review_url)


def notify_applicant_of_result(event_detail: EventDetail, request=None) -> None:
    """
    申請結果を申請者に通知する

    Args:
        event_detail: 承認/却下されたEventDetailインスタンス
        request: HTTPリクエスト（絶対URL生成用）
    """
    applicant = event_detail.applicant
    if not applicant or not applicant.email:
        logger.warning(
            f"申請結果通知: 申請者またはメールアドレスがありません。EventDetail ID={event_detail.pk}"
        )
        return

    community = event_detail.event.community

    # イベント詳細ページのURL
    detail_path = reverse('event:detail', kwargs={'pk': event_detail.pk})
    if request:
        detail_url = request.build_absolute_uri(detail_path)
    else:
        detail_url = f"https://vrc-ta-hub.com{detail_path}"

    context = {
        'applicant': applicant,
        'community': community,
        'event_detail': event_detail,
        'event': event_detail.event,
        'detail_url': detail_url,
        'is_approved': event_detail.status == 'approved',
    }

    if event_detail.status == 'approved':
        subject = f"[{community.name}] LT申請が承認されました"
    else:
        subject = f"[{community.name}] LT申請が却下されました"

    html_message = render_to_string(
        'event/email/lt_application_result.html', context
    )

    try:
        sent = send_mail(
            subject=subject,
            message='',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[applicant.email],
            html_message=html_message,
        )
        if sent:
            logger.info(
                f"申請結果通知メール送信成功: {applicant.email} (申請ID={event_detail.pk}, "
                f"status={event_detail.status})"
            )
        else:
            logger.warning(
                f"申請結果通知メール送信失敗: {applicant.email} (申請ID={event_detail.pk})"
            )
    except Exception as e:
        logger.error(
            f"申請結果通知メール送信エラー: {applicant.email} (申請ID={event_detail.pk}): {e}"
        )

    # Discord Webhook通知
    _send_discord_notification_for_result(event_detail)


def _send_discord_notification_for_new_application(
    event_detail: EventDetail, review_url: str
) -> None:
    """新しい申請のDiscord Webhook通知を送信"""
    community = event_detail.event.community
    webhook_url = community.notification_webhook_url

    if not webhook_url:
        return

    message = {
        "embeds": [{
            "title": "新しいLT申請",
            "color": 3447003,  # 青色
            "fields": [
                {"name": "集会", "value": community.name, "inline": True},
                {"name": "開催日", "value": str(event_detail.event.date), "inline": True},
                {"name": "テーマ", "value": event_detail.theme, "inline": False},
                {"name": "発表者", "value": event_detail.speaker, "inline": True},
                {
                    "name": "発表時間",
                    "value": f"{event_detail.duration}分",
                    "inline": True
                },
            ],
            "footer": {"text": "VRC技術学術系Hub"},
        }],
        "content": f"[申請を確認する]({review_url})",
    }

    try:
        response = requests.post(
            webhook_url, json=message, timeout=DISCORD_TIMEOUT_SECONDS
        )
        if response.ok:
            logger.info(
                f"Discord Webhook通知成功（新規申請）: Community={community.name}"
            )
        else:
            logger.warning(
                f"Discord Webhook通知失敗（新規申請）: status={response.status_code}"
            )
    except Exception as e:
        logger.error(f"Discord Webhook通知エラー（新規申請）: {e}")


def _send_discord_notification_for_result(event_detail: EventDetail) -> None:
    """申請結果のDiscord Webhook通知を送信"""
    community = event_detail.event.community
    webhook_url = community.notification_webhook_url

    if not webhook_url:
        return

    is_approved = event_detail.status == 'approved'
    status_text = "承認されました" if is_approved else "却下されました"
    color = 5763719 if is_approved else 15548997  # 緑 or 赤

    fields = [
        {"name": "集会", "value": community.name, "inline": True},
        {"name": "開催日", "value": str(event_detail.event.date), "inline": True},
        {"name": "テーマ", "value": event_detail.theme, "inline": False},
        {"name": "発表者", "value": event_detail.speaker, "inline": True},
        {"name": "ステータス", "value": status_text, "inline": True},
    ]

    if not is_approved and event_detail.rejection_reason:
        fields.append({
            "name": "却下理由",
            "value": event_detail.rejection_reason,
            "inline": False
        })

    message = {
        "embeds": [{
            "title": f"LT申請が{status_text}",
            "color": color,
            "fields": fields,
            "footer": {"text": "VRC技術学術系Hub"},
        }],
    }

    try:
        response = requests.post(
            webhook_url, json=message, timeout=DISCORD_TIMEOUT_SECONDS
        )
        if response.ok:
            logger.info(
                f"Discord Webhook通知成功（申請結果）: Community={community.name}, "
                f"status={event_detail.status}"
            )
        else:
            logger.warning(
                f"Discord Webhook通知失敗（申請結果）: status={response.status_code}"
            )
    except Exception as e:
        logger.error(f"Discord Webhook通知エラー（申請結果）: {e}")
