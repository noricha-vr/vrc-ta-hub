"""Community 管理フォーム周辺の副作用を扱う処理."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, timedelta

import requests
from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.core.cache import cache
from django.core.mail import send_mail
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from event.community_cleanup import cleanup_community_future_data

from .models import Community, CommunityMember

logger = logging.getLogger(__name__)

CleanupCommunityFutureData = Callable[..., dict[str, int]]
UserLike = AbstractBaseUser | AnonymousUser


def refresh_calendar_entry_and_event_cache(community: Community) -> None:
    """Community 更新後のカレンダー関連データとキャッシュを更新する."""
    calendar_entry = getattr(community, 'calendar_entry', None)
    if calendar_entry:
        calendar_entry.save()
    for event in community.events.all():
        cache_key = f'calendar_entry_url_{event.id}'
        cache.delete(cache_key)


def create_owner_membership(community: Community, user: UserLike) -> CommunityMember:
    """新規作成した集会にオーナーメンバーを追加する."""
    return CommunityMember.objects.create(
        community=community,
        user=user,
        role=CommunityMember.Role.OWNER,
    )


def notify_new_community_registration(community: Community, request: HttpRequest) -> None:
    """新規集会登録を Discord Webhook へ通知する."""
    if not settings.DISCORD_WEBHOOK_URL:
        return

    waiting_list_url = request.build_absolute_uri(reverse('community:waiting_list'))
    discord_message = {
        "content": f"**【新規集会登録】** {community.name}\n"
                   f"承認ページ: {waiting_list_url}"
    }
    discord_timeout_seconds = 10
    try:
        requests.post(settings.DISCORD_WEBHOOK_URL, json=discord_message, timeout=discord_timeout_seconds)
    except Exception as e:
        logger.warning(f'Discord通知送信失敗: {e}')


def approve_community_registration(community: Community, request: HttpRequest) -> None:
    """集会を承認し、オーナーへ承認メールを送信する."""
    community.status = 'approved'
    community.save()

    subject = f'{community.name}が承認されました'
    my_list_url = request.build_absolute_uri(reverse('event:my_list'))
    context = {
        'community': community,
        'my_list_url': my_list_url,
        'owner_name': community.get_owner().user_name if community.get_owner() else None,
    }
    html_message = render_to_string('community/email/accept.html', context)

    owner_email = community.get_owner_email()
    if owner_email:
        sent = send_mail(
            subject=subject,
            message='',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[owner_email],
            html_message=html_message,
        )
        if sent:
            logger.info(f'承認メール送信成功: {community.name} to {owner_email}')
        else:
            logger.warning(f'承認メール送信失敗: {community.name} to {owner_email}')
    else:
        logger.warning(f'承認メール送信スキップ: {community.name} - オーナーのメールアドレスが見つかりません')


def reject_community_registration(community: Community) -> None:
    """集会を非承認にし、オーナーへ非承認メールを送信する."""
    community.status = 'rejected'
    community.save()

    subject = f'{community.name}が非承認になりました'
    context = {
        'community': community,
        'owner_name': community.get_owner().user_name if community.get_owner() else None,
    }
    html_message = render_to_string('community/email/reject.html', context)

    owner_email = community.get_owner_email()
    if owner_email:
        sent = send_mail(
            subject=subject,
            message='',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[owner_email],
            html_message=html_message,
        )
        if sent:
            logger.info(f'非承認メール送信成功: {community.name} to {owner_email}')
        else:
            logger.warning(f'非承認メール送信失敗: {community.name} to {owner_email}')
    else:
        logger.warning(f'非承認メール送信スキップ: {community.name} - オーナーのメールアドレスが見つかりません')


def close_community_and_cleanup(
    community: Community,
    *,
    cleanup_func: CleanupCommunityFutureData = cleanup_community_future_data,
) -> dict[str, int]:
    """集会を閉鎖し、翌日以降の関連イベントと外部カレンダーを削除する."""
    today = timezone.now().date()
    community.end_at = today
    community.save()

    stats = cleanup_func(
        community=community,
        from_date=today + timedelta(days=1),
        delete_rules=True,
        delete_google_events=True,
        google_window_days=365,
        google_years=1,
    )
    logger.info(
        f'集会「{community.name}」を閉鎖しました。'
        f'削除イベント数={stats["db_events"]}、'
        f'削除定期ルール数={stats["rules"]}、'
        f'削除Googleイベント数={stats["google_events"]}'
    )
    return stats


def cleanup_closed_community(
    community: Community,
    *,
    cleanup_func: CleanupCommunityFutureData = cleanup_community_future_data,
) -> dict[str, int]:
    """管理者操作として閉鎖状態を保証し、翌日以降の関連データを削除する."""
    today: date = timezone.now().date()
    if community.end_at is None:
        community.end_at = today
        community.save(update_fields=['end_at'])

    return cleanup_func(
        community=community,
        from_date=today + timedelta(days=1),
        delete_rules=True,
        delete_google_events=True,
        google_window_days=365,
        google_years=1,
    )
