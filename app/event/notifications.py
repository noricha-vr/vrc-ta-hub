"""発表申請の通知サービス"""
import logging

import requests
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.timezone import localtime

from event.models import EventDetail
from event.youtube_urls import youtube_video_id
from website.constants import build_site_url
from website.discord_delivery import DiscordDelivery, post_discord_bot, select_community_discord_delivery
from website.discord_webhook import (
    get_webhook_error_context,
    post_discord_webhook,
)

logger = logging.getLogger(__name__)


def _post_discord_notification(
    delivery: DiscordDelivery, payload: dict, community_id: int,
) -> requests.Response | None:
    """選択済みの送り先へ送る。Bot の 403/404 だけ、この通知を Webhook に退避する。"""
    if delivery.kind == 'bot':
        try:
            response = post_discord_bot(delivery.channel_id, payload)
        except requests.HTTPError as error:
            _, status_code = get_webhook_error_context(error)
            if status_code not in (403, 404):
                raise
            logger.warning(
                'discord_bot_delivery_fallback community_id=%s status_code=%s count=1',
                community_id, status_code,
                extra={'event_type': 'discord_bot_delivery_fallback', 'community_id': community_id,
                       'status_code': status_code, 'count': 1},
            )
            if not delivery.webhook_url:
                return None
        else:
            logger.info(
                'discord_bot_delivery_sent community_id=%s status_code=%s count=1',
                community_id, response.status_code,
                extra={'event_type': 'discord_bot_delivery_sent', 'community_id': community_id,
                       'status_code': response.status_code, 'count': 1},
            )
            return response
    return post_discord_webhook(delivery.webhook_url, payload)


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


def notify_applicant_of_result(
    event_detail: EventDetail,
    request=None,
    schedule_changes: list[dict[str, str]] | None = None,
) -> None:
    """
    申請結果を申請者に通知する

    Args:
        event_detail: 承認/却下されたEventDetailインスタンス
        request: HTTPリクエスト（絶対URL生成用）
        schedule_changes: 主催者が承認時に変更した日時の一覧。
            ``[{'label': '開催日', 'before': ..., 'after': ...}, ...]`` 形式。
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

    guide_path = reverse('guide:page', kwargs={'path': 'speaker/slide-video'})
    if request:
        guide_url = request.build_absolute_uri(guide_path)
    else:
        guide_url = build_site_url(guide_path)

    article_url = ''
    if event_detail.status == 'approved':
        published_detail = EventDetail.all_objects.filter(
            EventDetail.article_notifiable_q(), pk=event_detail.pk, status='approved',
        ).first()
        if published_detail and published_detail.article_state() != EventDetail.ArticleState.NONE:
            article_path = reverse('event:detail', kwargs={'pk': event_detail.pk})
            article_url = request.build_absolute_uri(article_path) if request else build_site_url(article_path)

    context = {
        'applicant': applicant,
        'community': community,
        'event_detail': event_detail,
        'event': event_detail.event,
        'detail_url': detail_url,
        'guide_url': guide_url,
        'list_url': list_url,
        'is_approved': event_detail.status == 'approved',
        'schedule_changes': schedule_changes or [],
        'article_url': article_url,
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
    _send_discord_notification_for_result(event_detail, schedule_changes)


def get_published_article_for_notification(pk: int) -> EventDetail | None:
    """通知できる公開記事を、送信直前の同意・公開状態と本文で読み直す。"""
    detail = EventDetail.all_objects.filter(
        EventDetail.article_notifiable_q(), pk=pk, status='approved',
    ).select_related('event__community', 'applicant').first()
    if detail is None or detail.article_state() == EventDetail.ArticleState.NONE:
        return None
    return detail


def notify_applicant_of_article_published(event_detail: EventDetail, recipient) -> bool:
    """自動生成した記事を発表者に知らせる（申請結果の通知と同じくメールと集会の Discord）。

    メールには確認・修正用の編集ページへのリンクを入れる。承認前の発表は記事がまだ公開されないため、
    メールの文面を「作成しました」に変え、集会の Discord には流さない。
    メールを送れなかった時は既存の記事公開 Discord にも流さない（送り直した時の重複を防ぐ）。
    動画と記事の告知を送る状態（``article_discord_replaced_by_video_notice``）の時は、
    告知が記事の公開を兼ねるため、ここではメールだけ送る。

    Args:
        event_detail: 記事を作った発表
        recipient: メールの宛先。発表者本人（Vket 由来の発表は申し込んだ人）。
            呼び出し側が event.material_upload_reminders.get_material_reminder_recipient で決める。

    Returns:
        メールを送れた時 True。呼び出し側は False なら通知日時を戻して送り直せるようにする。
    """
    is_published = event_detail.status == 'approved'
    edit_url = build_site_url(
        reverse('account:lt_application_edit', kwargs={'pk': event_detail.pk})
    )
    article_url = build_site_url(reverse('event:detail', kwargs={'pk': event_detail.pk}))

    sent = _send_article_published_email(event_detail, recipient, edit_url, article_url, is_published)
    if sent and is_published:
        published_detail = get_published_article_for_notification(event_detail.pk)
        if published_detail is not None and not article_discord_replaced_by_video_notice(published_detail):
            send_discord_notification_for_article(published_detail, edit_url, article_url)
    return sent


def _send_article_published_email(
    event_detail: EventDetail, recipient, edit_url: str, article_url: str, is_published: bool
) -> bool:
    """記事を作成したことを発表者にメールで知らせる。送れた時 True."""
    if not recipient or not recipient.email:
        logger.warning(
            "記事公開通知: 宛先またはメールアドレスがありません。EventDetail ID=%s",
            event_detail.pk,
        )
        return False

    community = event_detail.event.community
    action = '公開しました' if is_published else '作成しました'
    context = {
        'applicant': recipient,
        'community': community,
        'event_detail': event_detail,
        'edit_url': edit_url,
        'article_url': article_url,
        'is_published': is_published,
    }
    html_message = render_to_string('event/email/article_published.html', context)

    try:
        sent = send_mail(
            subject=f"[{community.name}] 発表の記事を{action}",
            message='',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient.email],
            html_message=html_message,
        )
        if sent:
            logger.info("記事公開通知メール送信成功: 申請ID=%s", event_detail.pk)
            return True
        logger.warning("記事公開通知メール送信失敗: 申請ID=%s", event_detail.pk)
        return False
    except Exception as e:
        logger.error("記事公開通知メール送信エラー: 申請ID=%s: %s", event_detail.pk, e)
        return False


def send_discord_notification_for_article(
    event_detail: EventDetail, edit_url: str, article_url: str
) -> bool:
    """記事の公開を集会の Discord に知らせる.

    Returns:
        送れなかった時だけ False（送信先が無い時は送る必要が無いので True）。
    """
    community = event_detail.event.community
    delivery = select_community_discord_delivery(community)
    if delivery.kind == 'none':
        return True

    message = {
        "embeds": [{
            "title": "📝 発表の記事を公開しました",
            "url": article_url,
            "description": f"**{event_detail.h1 or event_detail.theme}**",
            "color": 3447003,
            "fields": [
                {"name": "👤 発表者", "value": event_detail.speaker, "inline": True},
                {"name": "📅 開催日", "value": str(event_detail.event.date), "inline": True},
                {"name": "✏️ 確認・修正（発表者向け）", "value": edit_url, "inline": False},
            ],
            "footer": {"text": community.name},
        }],
        "allowed_mentions": {"parse": []},
    }

    try:
        if _post_discord_notification(delivery, message, community.pk) is None:
            return True
        logger.info(
            "Discord通知成功（記事公開）: community_id=%s event_detail_id=%s",
            community.pk,
            event_detail.pk,
        )
        return True
    except Exception as error:
        error_type, status_code = get_webhook_error_context(error)
        logger.error(
            "Discord通知エラー（記事公開）: "
            "community_id=%s event_detail_id=%s error_type=%s status_code=%s",
            community.pk,
            event_detail.pk,
            error_type,
            status_code,
        )
        return False


def notify_video_article_published(event_detail: EventDetail) -> bool:
    """動画と記事の公開を集会へ告知する。送信失敗だけ False、送信先なしは True。

    重複を防ぐ担当の取得は ``announce_video_article_if_ready`` が行う（ここは送るだけ）。
    """
    community = event_detail.event.community
    delivery = select_community_discord_delivery(community)
    if delivery.kind == 'none':
        return True

    article_url = build_site_url(reverse('event:detail', kwargs={'pk': event_detail.pk}))
    embed = {
        "title": event_detail.h1 or event_detail.theme,
        "url": article_url,
        "color": 3447003,
        "fields": [
            {"name": "👤 発表者", "value": event_detail.speaker, "inline": True},
            {"name": "📅 開催日", "value": str(event_detail.event.date), "inline": True},
            {"name": "🎥 動画", "value": event_detail.youtube_url, "inline": False},
            {"name": "📝 記事", "value": article_url, "inline": False},
        ],
        "footer": {"text": community.name},
    }
    if event_detail.article_state() == EventDetail.ArticleState.AUTO:
        edit_url = build_site_url(reverse('account:lt_application_edit', kwargs={'pk': event_detail.pk}))
        embed["fields"].append({"name": "✏️ 確認・修正（発表者向け）", "value": edit_url, "inline": False})
    description = event_detail.visible_meta_description
    if description:
        embed["description"] = description[:200] + ('…' if len(description) > 200 else '')
    image_url = _get_event_detail_og_image_url(event_detail)
    if image_url:
        embed["image"] = {"url": image_url}
    message = {
        "content": f"🎬 {event_detail.speaker} さんの発表「{event_detail.theme}」の動画と記事を公開しました",
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }
    try:
        if _post_discord_notification(delivery, message, community.pk) is None:
            return True
        logger.info(
            "Discord通知成功（動画と記事公開）: community_id=%s event_detail_id=%s",
            community.pk, event_detail.pk,
        )
        return True
    except Exception as error:
        error_type, status_code = get_webhook_error_context(error)
        logger.error(
            "Discord通知エラー（動画と記事公開）: "
            "community_id=%s event_detail_id=%s error_type=%s status_code=%s",
            community.pk, event_detail.pk, error_type, status_code,
        )
        return False


def article_discord_replaced_by_video_notice(event_detail: EventDetail) -> bool:
    """先に動画と記事の告知を試み、今の動画で告知済みの時だけ真（既存の記事公開の Discord を送らない）。

    告知は担当を UPDATE の条件で取るので、保存側と重なっても 1 回しか送らない。
    告知に失敗して列が戻った時や告知の対象でない時は偽になり、既存の Discord を送る。
    例外は記録して偽を返し、メールや既存の通知を止めない。
    """
    announce_video_article_if_ready(event_detail.pk)
    try:
        current = EventDetail.all_objects.filter(pk=event_detail.pk).values(
            'youtube_url', 'video_article_announced_id',
        ).first()
    except Exception:
        logger.exception(
            'video_article_announcement_check_failed',
            extra={'event_type': 'video_article_announcement_check_failed', 'event_detail_id': event_detail.pk},
        )
        return False
    if current is None:
        return False
    video_id = youtube_video_id(current['youtube_url'])
    return bool(video_id) and current['video_article_announced_id'] == video_id


def announce_video_article_if_ready(pk: int) -> None:
    """告知してよい状態なら、その動画で 1 回だけ動画と記事の公開を告知する。

    行を読み直し、告知済みの動画 ID の列を条件付きの UPDATE で入れられた時だけ送る。
    送れなかった時は自分が入れた値の時だけ元に戻し、次の保存で送り直せるようにする。
    例外は記録して呼び出し元（保存のコミット後の処理）には投げない。

    制限:
        - 字幕が無く動画を使わずに作った自動生成の記事（生成元の動画 ID が空）は告知しない。
        - 初めての生成では PDF のサムネイルができる前に告知するため、画像が集会のポスターになることがある。
    """
    try:
        detail = EventDetail.all_objects.select_related('event__community').filter(pk=pk).first()
        if detail is None or not detail.video_article_ready():
            return
        video_id = detail.video_id
        previous = detail.video_article_announced_id
        claimed = EventDetail.all_objects.filter(
            pk=pk, video_article_announced_id=previous,
        ).exclude(video_article_announced_id=video_id).update(video_article_announced_id=video_id)
        if not claimed:
            return
        if not _send_video_article_notice(detail):
            EventDetail.all_objects.filter(
                pk=pk, video_article_announced_id=video_id,
            ).update(video_article_announced_id=previous)
    except Exception:
        logger.exception(
            'video_article_announcement_failed',
            extra={'event_type': 'video_article_announcement_failed', 'event_detail_id': pk},
        )


def _send_video_article_notice(detail: EventDetail) -> bool:
    """告知を送る。画像の URL を作る時などの例外も失敗として扱う（例外の本文は記録しない）。"""
    try:
        return notify_video_article_published(detail)
    except Exception:
        logger.exception(
            'video_article_notice_failed',
            extra={'event_type': 'video_article_notice_failed', 'event_detail_id': detail.pk},
        )
        return False


def _send_discord_notification_for_new_application(
    event_detail: EventDetail, review_url: str
) -> None:
    """新しい申請のDiscord通知を送信"""
    community = event_detail.event.community
    delivery = select_community_discord_delivery(community)

    if delivery.kind == 'none':
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
        response = _post_discord_notification(delivery, message, community.pk)
        if response is None:
            return
        logger.info(
            "Discord通知成功（新規申請）: community_id=%s event_detail_id=%s status_code=%s",
            community.pk, event_detail.pk, response.status_code,
        )
    except requests.RequestException as error:
        # gateway で回復できなかった最終失敗のみここに到達する
        error_type, status_code = get_webhook_error_context(error)
        logger.error(
            "Discord通知エラー（新規申請、リトライ後）: "
            "community_id=%s event_detail_id=%s error_type=%s status_code=%s",
            community.pk,
            event_detail.pk,
            error_type,
            status_code,
        )
    except Exception as error:
        error_type, status_code = get_webhook_error_context(error)
        logger.error(
            "Discord通知エラー（新規申請）: "
            "community_id=%s event_detail_id=%s error_type=%s status_code=%s",
            community.pk,
            event_detail.pk,
            error_type,
            status_code,
        )


def _send_discord_notification_for_result(
    event_detail: EventDetail,
    schedule_changes: list[dict[str, str]] | None = None,
) -> None:
    """申請結果のDiscord通知を送信"""
    community = event_detail.event.community
    delivery = select_community_discord_delivery(community)

    if delivery.kind == 'none':
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

    if is_approved:
        fields.append({
            "name": "⏰ 開始時刻",
            "value": event_detail.start_time.strftime("%H:%M"),
            "inline": True,
        })
        if schedule_changes:
            fields.append({
                "name": "🔄 日時変更",
                "value": "\n".join(
                    f"{change['label']}: {change['before']} → {change['after']}"
                    for change in schedule_changes
                ),
                "inline": False,
            })

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
        if _post_discord_notification(delivery, message, community.pk) is None:
            return
        logger.info(
            "Discord通知成功（申請結果）: community_id=%s event_detail_id=%s",
            community.pk, event_detail.pk,
        )
    except requests.RequestException as error:
        # gateway で回復できなかった最終失敗のみここに到達する
        error_type, status_code = get_webhook_error_context(error)
        logger.error(
            "Discord通知エラー（申請結果、リトライ後）: "
            "community_id=%s event_detail_id=%s error_type=%s status_code=%s",
            community.pk,
            event_detail.pk,
            error_type,
            status_code,
        )
    except Exception as error:
        error_type, status_code = get_webhook_error_context(error)
        logger.error(
            "Discord通知エラー（申請結果）: "
            "community_id=%s event_detail_id=%s error_type=%s status_code=%s",
            community.pk,
            event_detail.pk,
            error_type,
            status_code,
        )


def notify_speaker_account_linked(event_detail: EventDetail, user) -> None:
    """発表へのアカウント紐づけを集会のDiscordへ通知する。"""
    community = event_detail.event.community
    delivery = select_community_discord_delivery(community)
    if delivery.kind == 'none':
        return

    # user_name の unique 制約解除で同名ユーザーが存在し得るため、登録日を添えて誤認を防ぐ。
    # email は「公開しない」とサインアップ時に約束しているため通知にも載せない。
    joined_on = localtime(user.date_joined).strftime("%Y/%m/%d")
    payload = {
        "content": (
            f"{event_detail.theme} に {user.display_label}（登録日 {joined_on}）が"
            "アカウントを紐づけました"
        ),
        "allowed_mentions": {"parse": []},
    }
    try:
        _post_discord_notification(delivery, payload, community.pk)
    except Exception as error:
        error_type, status_code = get_webhook_error_context(error)
        logger.warning(
            "Discord通知エラー（発表者紐づけ）: "
            "community_id=%s event_detail_id=%s error_type=%s status_code=%s",
            community.pk,
            event_detail.pk,
            error_type,
            status_code,
        )


def notify_slide_material_published(event_detail: EventDetail) -> None:
    """資料公開時のDiscord通知を送信する."""
    community = event_detail.event.community
    delivery = select_community_discord_delivery(community)

    if delivery.kind == 'none':
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
        if _post_discord_notification(delivery, message, community.pk) is None:
            return
        logger.info(
            "Discord通知成功（資料公開）: community_id=%s event_detail_id=%s",
            community.pk,
            event_detail.pk,
        )
    except requests.RequestException as error:
        # gateway で回復できなかった最終失敗のみここに到達する
        error_type, status_code = get_webhook_error_context(error)
        logger.error(
            "Discord通知エラー（資料公開、リトライ後）: "
            "community_id=%s event_detail_id=%s error_type=%s status_code=%s",
            community.pk,
            event_detail.pk,
            error_type,
            status_code,
        )
    except Exception as error:
        error_type, status_code = get_webhook_error_context(error)
        logger.error(
            "Discord通知エラー（資料公開）: "
            "community_id=%s event_detail_id=%s error_type=%s status_code=%s",
            community.pk,
            event_detail.pk,
            error_type,
            status_code,
        )
