"""予約済みの文章だけを固定のDiscord Webhookへ送る。"""

import math
import re
from datetime import timedelta

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from .models import ScheduledDiscordPost, validate_discord_content


_WEBHOOK_URL = re.compile(r"https://discord\.com/api(?:/v\d+)?/webhooks/\d+/[A-Za-z0-9._-]+/?")
_CHANNEL_URL = re.compile(r"https://discord\.com/channels/\d+/\d+/?")
_SENDING_TIMEOUT = timedelta(minutes=5)
_REQUEST_TIMEOUT = (5, 20)


def _configuration() -> tuple[str, str]:
    webhook_url = (getattr(settings, "DISCORD_SCHEDULED_WEBHOOK_URL", "") or "").strip()
    channel_url = (getattr(settings, "DISCORD_SCHEDULED_CHANNEL_URL", "") or "").strip()
    if not _WEBHOOK_URL.fullmatch(webhook_url) or not _CHANNEL_URL.fullmatch(channel_url):
        raise ValueError("Discordの予約投稿先が正しく設定されていません。管理者に設定を確認してください。")
    return webhook_url.rstrip("/"), channel_url.rstrip("/")


def is_configured() -> bool:
    """画面と送信処理で同じ設定チェックを使う。秘密のURLは画面に返さない。"""
    try:
        _configuration()
    except ValueError:
        return False
    return True


def _due_posts(now):
    return ScheduledDiscordPost.objects.filter(
        status=ScheduledDiscordPost.Status.SCHEDULED,
        scheduled_at__lte=now,
    ).filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))


def _webhook_is_rate_limited(now) -> bool:
    # 固定Webhookの待機は、429後に追加された予約にも適用する。
    # 元の予約が取り消されてもDiscordの待ち時間は解除されない。
    return ScheduledDiscordPost.objects.filter(next_attempt_at__gt=now).exists()


def _finish(post, status, *, error_message="", message_url=""):
    now = timezone.now()
    ScheduledDiscordPost.objects.filter(
        pk=post.pk,
        status__in=[ScheduledDiscordPost.Status.SENDING, ScheduledDiscordPost.Status.NEEDS_REVIEW],
        started_at=post.started_at,
    ).update(
        status=status,
        error_message=error_message,
        message_url=message_url,
        sent_at=now if status == ScheduledDiscordPost.Status.SENT else None,
        next_attempt_at=None,
        updated_at=now,
    )


def _retry_after(response):
    """429の待ち時間だけを解釈する。応答本文は保存・記録しない。"""
    try:
        data = response.json()
    except ValueError:
        data = {}
    values = [data.get("retry_after") if isinstance(data, dict) else None, response.headers.get("Retry-After")]
    for value in values:
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(seconds) and 0 <= seconds <= timedelta(days=7).total_seconds():
            return max(seconds, 1)
    return None


def _defer(post, seconds):
    now = timezone.now()
    retry_at = now + timedelta(seconds=seconds)
    ScheduledDiscordPost.objects.filter(
        pk=post.pk,
        status=ScheduledDiscordPost.Status.SENDING,
        started_at=post.started_at,
    ).update(
        status=ScheduledDiscordPost.Status.SCHEDULED,
        started_at=None,
        next_attempt_at=retry_at,
        error_message="Discordの送信制限により待機中です。解除後に自動で再試行します。",
        updated_at=now,
    )
    # 同じ固定Webhookへ続けて送らず、他の予約もDiscord指定の待ち時間を守る。
    ScheduledDiscordPost.objects.filter(status=ScheduledDiscordPost.Status.SCHEDULED).filter(
        Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lt=retry_at)
    ).update(next_attempt_at=retry_at, updated_at=now)


def _send_claimed_post(post) -> str:
    try:
        webhook_url, channel_url = _configuration()
        validate_discord_content(post.content)
    except ValueError as error:
        _finish(post, ScheduledDiscordPost.Status.FAILED, error_message=str(error))
        return "failed"
    except ValidationError as error:
        _finish(post, ScheduledDiscordPost.Status.FAILED, error_message=" ".join(error.messages))
        return "failed"

    try:
        response = requests.post(
            webhook_url,
            params={"wait": "true"},
            json={
                "content": post.content,
                # 運営が本文に明示したメンションだけをDiscordに解釈させる。
                "allowed_mentions": {"parse": ["users", "roles", "everyone"]},
            },
            timeout=_REQUEST_TIMEOUT,
            allow_redirects=False,
        )
    except requests.RequestException:
        # 例外文字列にはWebhookの秘密tokenを含むURLが入る場合がある。
        # サーバーが受理した後に応答が失われた可能性があるので自動再送しない。
        _finish(
            post,
            ScheduledDiscordPost.Status.NEEDS_REVIEW,
            error_message="通信が完了せず、投稿できたか確認できませんでした。Discordで投稿を確認してください。自動再送はしません。",
        )
        return "needs_review"

    if response.status_code == 429:
        seconds = _retry_after(response)
        if seconds is not None:
            _defer(post, seconds)
            return "deferred"
        _finish(
            post,
            ScheduledDiscordPost.Status.FAILED,
            error_message="Discordの送信制限を受けましたが、待ち時間を確認できませんでした。時間を置いて予約し直してください。",
        )
        return "failed"

    if 200 <= response.status_code < 300:
        try:
            data = response.json()
        except ValueError:
            data = {}
        message_id = data.get("id") if isinstance(data, dict) else None
        channel_id = data.get("channel_id") if isinstance(data, dict) else None
        if isinstance(message_id, str) and re.fullmatch(r"[0-9]+", message_id):
            if channel_id is None or channel_id == channel_url.rsplit("/", 1)[-1]:
                _finish(post, ScheduledDiscordPost.Status.SENT, message_url=f"{channel_url}/{message_id}")
                return "sent"
        _finish(
            post,
            ScheduledDiscordPost.Status.NEEDS_REVIEW,
            error_message="Discordから応答がありましたが、投稿先や投稿IDを確認できませんでした。Discordで投稿を確認してください。自動再送はしません。",
        )
        return "needs_review"

    if response.status_code >= 500:
        _finish(
            post,
            ScheduledDiscordPost.Status.NEEDS_REVIEW,
            error_message="Discord側でエラーが発生し、投稿できたか確認できませんでした。Discordで投稿を確認してください。自動再送はしません。",
        )
        return "needs_review"

    _finish(
        post,
        ScheduledDiscordPost.Status.FAILED,
        error_message=f"Discordが投稿を受け付けませんでした（HTTP {response.status_code}）。投稿先の設定・権限と本文を確認してください。",
    )
    return "failed"


def process_scheduled_posts(limit: int = 20) -> dict[str, int]:
    """期限が来た予約をclaimして送る。並行実行や不明な結果を自動再送しない。"""
    now = timezone.now()
    counts = {"processed": 0, "sent": 0, "failed": 0, "needs_review": 0, "deferred": 0}
    counts["needs_review"] = ScheduledDiscordPost.objects.filter(status=ScheduledDiscordPost.Status.SENDING).filter(
        Q(started_at__lt=now - _SENDING_TIMEOUT) | Q(started_at__isnull=True)
    ).update(
        status=ScheduledDiscordPost.Status.NEEDS_REVIEW,
        error_message="送信処理が途中で停止した可能性があります。Discordで投稿を確認してください。自動再送はしません。",
        updated_at=now,
    )
    if _webhook_is_rate_limited(now):
        return counts
    ids = list(_due_posts(now).order_by("scheduled_at", "pk").values_list("pk", flat=True)[:limit])
    for post_id in ids:
        started_at = timezone.now()
        if _webhook_is_rate_limited(started_at):
            break
        # 本文取得前に条件付きUPDATEする。予約の編集・取消も同じstatus条件を使う。
        # 外部への通信中はDBトランザクションや行ロックを保持しない。
        claimed = _due_posts(started_at).filter(pk=post_id).update(
            status=ScheduledDiscordPost.Status.SENDING,
            started_at=started_at,
            next_attempt_at=None,
            error_message="",
            updated_at=started_at,
        )
        if not claimed:
            continue
        post = ScheduledDiscordPost.objects.get(pk=post_id)
        counts["processed"] += 1
        result = _send_claimed_post(post)
        counts[result] += 1
        if result == "deferred":
            break
    return counts
