"""Django シグナル: 集会承認・LT/特別回承認時に TweetQueue へ自動追加"""

import logging

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from community.models import Community
from event.models import EventDetail

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Community)
def track_community_status_change(sender, instance, **kwargs):
    """Community のステータス変更を追跡する。

    post_save で旧値を参照できるよう _old_status を instance に保持する。
    """
    if instance.pk:
        try:
            old = Community.objects.get(pk=instance.pk)
            instance._old_status = old.status
        except Community.DoesNotExist:
            instance._old_status = None
    else:
        instance._old_status = None


@receiver(pre_save, sender=EventDetail)
def track_event_detail_status_change(sender, instance, **kwargs):
    """EventDetail のステータス変更を追跡する。

    post_save で旧値を参照できるよう _old_status を instance に保持する。
    """
    if instance.pk:
        try:
            old = EventDetail.objects.get(pk=instance.pk)
            instance._old_status = old.status
        except EventDetail.DoesNotExist:
            instance._old_status = None
    else:
        instance._old_status = None


@receiver(post_save, sender=Community)
def queue_new_community_tweet(sender, instance, created, **kwargs):
    """Community が承認された時にツイートキューに追加する。

    pending -> approved への遷移時のみトリガーされる。
    同一 community の重複キューは作成しない。
    """
    # 遅延インポートで循環インポートを回避
    from twitter.models import TweetQueue

    old_status = getattr(instance, "_old_status", None)

    # approved 以外、または既に approved だった場合はスキップ
    if instance.status != "approved" or old_status == "approved":
        return

    # 重複チェック
    if TweetQueue.objects.filter(community=instance, tweet_type="new_community").exists():
        return

    # 初回イベントを取得
    from django.utils import timezone
    from event.models import Event

    first_event = (
        Event.objects.filter(community=instance, date__gte=timezone.now().date())
        .order_by("date", "start_time")
        .first()
    )

    TweetQueue.objects.create(
        tweet_type="new_community",
        community=instance,
        event=first_event,
    )
    logger.info("Queued new community tweet: %s", instance.name)


@receiver(post_save, sender=EventDetail)
def queue_event_detail_tweet(sender, instance, created, **kwargs):
    """LT/特別回の EventDetail が承認された時にツイートキューに追加する。

    以下の場合にキューを追加する:
    - 新規作成 (created=True) かつ status='approved'
    - 既存更新で _old_status != 'approved' から status='approved' に遷移

    detail_type が 'LT' or 'SPECIAL' の場合のみ。
    同一 event_detail の重複キューは作成しない。
    """
    from twitter.models import TweetQueue

    if instance.detail_type not in ("LT", "SPECIAL"):
        return

    if instance.status != "approved":
        return

    old_status = getattr(instance, "_old_status", None)

    # 既に approved だった場合はスキップ（新規作成時は old_status=None なので通過）
    if not created and old_status == "approved":
        return

    tweet_type = "lt" if instance.detail_type == "LT" else "special"

    # 重複チェック
    if TweetQueue.objects.filter(event_detail=instance, tweet_type=tweet_type).exists():
        return

    TweetQueue.objects.create(
        tweet_type=tweet_type,
        community=instance.event.community,
        event=instance.event,
        event_detail=instance,
    )
    logger.info("Queued %s tweet: %s - %s", tweet_type, instance.speaker, instance.theme)
