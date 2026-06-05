"""発表申請の通知サービス"""
import logging

import requests
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse

from event.models import EventDetail
from website.constants import build_site_url

logger = logging.getLogger(__name__)

# Discord Webhook送信タイムアウト（秒）
DISCORD_TIMEOUT_SECONDS = 10


def _build_absolute_url(url: str) -> str:
    """Discord が取得できる絶対 URL に整形する."""
    return build_site_url(url) if url else ""


def _get_file_url(file_field) -> str:
    """未設定ファイルを避けて URL を取得する."""
    if not file_field:
        return ""
    try:
        return _build_absolute_url(file_field.url)
    except ValueError:
        return ""


def _get_event_detail_og_image_url(event_detail: EventDetail) -> str:
    """EventDetail ページの OGP と同じ優先順位で画像 URL を返す."""
    thumbnail_url = _get_file_url(event_detail.thumbnail_image)
    if thumbnail_url:
        return thumbnail_url
    return _get_file_url(event_detail.event.community.poster_image)


def notify_owners_of_new_application(event_detail: EventDetail, request=None) -> None:
    """
    新しい発表申請を主催者に通知する

    Args:
        event_detail: 申請されたEventDetailインスタンス
        request: HTTPリクエスト（絶対URL生成用）
    """
    community = event_detail.event.community
    owners = community.get_owners()

    if not owners:
        logger.warning(
            f"発表申請通知: 主催者が見つかりません。Community={community.name}"
        )
        return

    # レビューページのURL
    review_path = reverse('event:lt_application_review', kwargs={'pk': event_detail.pk})
    if request:
        review_url = request.build_absolute_uri(review_path)
    else:
        review_url = build_site_url(review_path)

    # メール送信
    for owner in owners:
        if not owner.email:
            logger.warning(
                f"発表申請通知: メールアドレスがありません。User={owner.user_name}"
            )
            continue

        context = {
            'owner': owner,
            'community': community,
            'event_detail': event_detail,
            'event': event_detail.event,
            'review_url': review_url,
        }

        subject = f"[{community.name}] 新しい発表申請があります"
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
                    f"発表申請通知メール送信成功: {owner.email} (申請ID={event_detail.pk})"
                )
            else:
                logger.warning(
                    f"発表申請通知メール送信失敗: {owner.email} (申請ID={event_detail.pk})"
                )
        except Exception as e:
            logger.error(
                f"発表申請通知メール送信エラー: {owner.email} (申請ID={event_detail.pk}): {e}"
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

    # 承認時: 発表申請編集ページ、却下時: 発表申請一覧ページ
    if event_detail.status == 'approved':
        detail_path = reverse('account:lt_application_edit', kwargs={'pk': event_detail.pk})
    else:
        detail_path = reverse('account:lt_application_list')
    if request:
        detail_url = request.build_absolute_uri(detail_path)
    else:
        detail_url = build_site_url(detail_path)

    # 却下時は一覧ページへのリンクも用意
    list_path = reverse('account:lt_application_list')
    if request:
        list_url = request.build_absolute_uri(list_path)
    else:
        list_url = build_site_url(list_path)

    context = {
        'applicant': applicant,
        'community': community,
        'event_detail': event_detail,
        'event': event_detail.event,
        'detail_url': detail_url,
        'list_url': list_url,
        'is_approved': event_detail.status == 'approved',
    }

    if event_detail.status == 'approved':
        subject = f"[{community.name}] 発表申請が承認されました"
    else:
        subject = f"[{community.name}] 発表申請が却下されました"

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

    # 申請者名を取得
    applicant_name = event_detail.applicant.display_label if event_detail.applicant else "不明"

    # 認知科学に基づくレイアウト:
    # 1. Call to Action を最初に（content）
    # 2. テーマを description で目立たせる
    # 3. 関連情報をグループ化（fields）
    # 4. コンテキスト情報を footer に
    fields = [
        {"name": "👤 発表者", "value": event_detail.speaker, "inline": True},
        {"name": "📅 開催日", "value": str(event_detail.event.date), "inline": True},
        {"name": "⏱️ 時間", "value": f"{event_detail.duration}分", "inline": True},
    ]

    # 追加情報があれば追加（Discord制限対応: 1024文字まで）
    if event_detail.additional_info:
        # Discordのフィールド値の制限は1024文字
        max_additional_info_length = 1000
        additional_info_value = event_detail.additional_info[:max_additional_info_length]
        if len(event_detail.additional_info) > max_additional_info_length:
            additional_info_value += "..."
        fields.append({
            "name": "📝 追加情報",
            "value": additional_info_value,
            "inline": False
        })

    message = {
        "content": f"⬇️ **承認/却下をお願いします**\n{review_url}",
        "embeds": [{
            "title": "📢 新しい発表申請",
            "description": f"**{event_detail.theme}**",
            "color": 16750848,  # オレンジ色（注目を引く）
            "fields": fields,
            "footer": {"text": f"{community.name} | 申請者: {applicant_name}"},
        }],
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

    # 認知科学に基づくレイアウト:
    # 結果を一目で分かるように、絵文字と色で視覚的に区別
    if is_approved:
        title = "✅ 発表申請が承認されました"
        color = 5763719  # 緑
    else:
        title = "❌ 発表申請が却下されました"
        color = 15548997  # 赤

    fields = [
        {"name": "👤 発表者", "value": event_detail.speaker, "inline": True},
        {"name": "📅 開催日", "value": str(event_detail.event.date), "inline": True},
    ]

    if not is_approved and event_detail.rejection_reason:
        fields.append({
            "name": "📝 却下理由",
            "value": event_detail.rejection_reason,
            "inline": False
        })

    message = {
        "embeds": [{
            "title": title,
            "description": f"**{event_detail.theme}**",
            "color": color,
            "fields": fields,
            "footer": {"text": community.name},
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


def notify_slide_material_published(event_detail: EventDetail) -> None:
    """資料公開時のDiscord Webhook通知を送信する."""
    community = event_detail.event.community
    webhook_url = community.notification_webhook_url

    if not webhook_url:
        return

    detail_url = build_site_url(reverse('event:detail', kwargs={'pk': event_detail.pk}))
    slide_file_url = ""
    if event_detail.slide_file:
        slide_file_url = _build_absolute_url(event_detail.slide_file.url)

    fields = [
        {"name": "👤 発表者", "value": event_detail.speaker, "inline": True},
        {"name": "📅 開催日", "value": str(event_detail.event.date), "inline": True},
        {"name": "🔗 イベント詳細", "value": detail_url, "inline": False},
    ]

    if event_detail.slide_url:
        fields.append({
            "name": "📄 スライドURL",
            "value": event_detail.slide_url,
            "inline": False,
        })

    if slide_file_url:
        fields.append({
            "name": "📎 アップロード済みPDF",
            "value": slide_file_url,
            "inline": False,
        })

    if event_detail.youtube_url:
        fields.append({
            "name": "🎥 YouTube",
            "value": event_detail.youtube_url,
            "inline": False,
        })

    embed = {
        "title": "登壇資料が公開されました",
        "url": detail_url,
        "description": f"**{event_detail.theme}**",
        "color": 3447003,
        "fields": fields,
        "footer": {"text": community.name},
    }
    image_url = _get_event_detail_og_image_url(event_detail)
    if image_url:
        embed["image"] = {"url": image_url}

    message = {
        "content": "📚 **資料公開のお知らせ**",
        "embeds": [embed],
    }

    try:
        response = requests.post(
            webhook_url, json=message, timeout=DISCORD_TIMEOUT_SECONDS,
        )
        if response.ok:
            logger.info(
                "Discord Webhook通知成功（資料公開）: Community=%s, EventDetail=%s",
                community.name,
                event_detail.pk,
            )
        else:
            logger.warning(
                "Discord Webhook通知失敗（資料公開）: status=%s, EventDetail=%s",
                response.status_code,
                event_detail.pk,
            )
    except Exception as e:
        logger.error(
            "Discord Webhook通知エラー（資料公開）: EventDetail=%s, error=%s",
            event_detail.pk,
            e,
        )
