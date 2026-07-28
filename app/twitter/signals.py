"""Django シグナル: 集会承認・LT/特別回承認時に TweetQueue へ自動追加。

当日開催の発表は個別告知をスキップ扱いにし、同時に daily_reminder を同期する。
"""

import logging

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from community.models import Community
from event.models import EventDetail
from twitter.scheduling import default_scheduled_at
from twitter.services.daily_reminder import (
    PRESENTATION_DETAIL_TYPES,
    _ensure_same_day_individual_queue_skipped,
    _is_active_presentation,
    _sync_daily_reminder_for_event,
    _sync_daily_reminders_for_instance,
)
from twitter.services.tweet_generation import (
    _start_tweet_generation,
    sync_slide_share_queue_image,
)

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Community)
def track_community_status_change(sender, instance, **kwargs):
    """Community の旧ステータスを保持する。"""
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
    """EventDetail の旧値を保持する。"""
    instance._old_status = None
    instance._old_slide_url = ""
    instance._old_youtube_url = ""
    instance._old_slide_file = ""
    instance._old_speaker = ""
    instance._old_theme = ""
    instance._old_start_time = None
    instance._old_detail_type = None
    instance._old_event_id = None
    instance._old_event_date = None

    if instance.pk:
        try:
            old = EventDetail.objects.select_related('event').only(
                'status', 'slide_url', 'youtube_url', 'slide_file', 'speaker', 'theme',
                'start_time', 'detail_type', 'event_id', 'event__date',
            ).get(pk=instance.pk)
            instance._old_status = old.status
            instance._old_slide_url = old.slide_url or ""
            instance._old_youtube_url = old.youtube_url or ""
            instance._old_slide_file = str(old.slide_file) if old.slide_file else ""
            instance._old_speaker = old.speaker or ""
            instance._old_theme = old.theme or ""
            instance._old_start_time = old.start_time
            instance._old_detail_type = old.detail_type
            instance._old_event_id = old.event_id
            instance._old_event_date = old.event.date
        except EventDetail.DoesNotExist:
            # 削除直後など旧値が存在しない正常系では差分なしとして続行する。
            pass

@receiver(post_save, sender=Community)
def queue_new_community_tweet(sender, instance, created, **kwargs):
    """Community が承認された時にツイートキューに追加する。"""
    try:
        _queue_new_community_tweet(instance, created)
    except Exception:
        logger.exception("Failed to queue new community tweet for %s", instance.pk)


def _queue_new_community_tweet(instance, created):
    from twitter.models import TweetQueue
    from event.models import Event

    old_status = getattr(instance, "_old_status", None)
    if instance.status != "approved" or old_status == "approved":
        return

    if TweetQueue.objects.filter(community=instance, tweet_type="new_community").exists():
        return

    first_event = (
        Event.objects.filter(community=instance, date__gte=timezone.localdate())
        .order_by("date", "start_time")
        .first()
    )

    queue_item = TweetQueue.objects.create(
        tweet_type="new_community",
        community=instance,
        event=first_event,
        scheduled_at=default_scheduled_at(tweet_type='new_community', event=first_event),
    )
    logger.info("Queued new community tweet: %s", instance.name)

    _start_tweet_generation(queue_item)


@receiver(post_save, sender=EventDetail)
def queue_slide_share_tweet(sender, instance, created, **kwargs):
    """スライド/記事が初めてアップロードされた時にツイートキューに追加する。"""
    try:
        _queue_slide_share_tweet(instance, created)
    except Exception:
        logger.exception("Failed to queue slide share tweet for EventDetail %s", instance.pk)


def _queue_slide_share_tweet(instance, created):
    from twitter.models import TweetQueue
    from twitter.tweet_generator import get_tweet_image_url

    if instance.detail_type not in PRESENTATION_DETAIL_TYPES:
        return

    if instance.status != "approved":
        return

    if instance.event.date >= timezone.localdate():
        return

    old_slide_url = getattr(instance, "_old_slide_url", "")
    old_youtube_url = getattr(instance, "_old_youtube_url", "")
    old_slide_file = getattr(instance, "_old_slide_file", "")
    new_slide_url = instance.slide_url or ""
    new_youtube_url = instance.youtube_url or ""
    new_slide_file = str(instance.slide_file) if instance.slide_file else ""

    slide_newly_set = not old_slide_url and new_slide_url
    youtube_newly_set = not old_youtube_url and new_youtube_url
    slide_file_newly_set = not old_slide_file and new_slide_file

    if not slide_newly_set and not youtube_newly_set and not slide_file_newly_set:
        return

    should_notify_slide_webhook = slide_newly_set or slide_file_newly_set
    if should_notify_slide_webhook:
        from event.notifications import notify_slide_material_published

        notify_slide_material_published(instance)

    existing_queue = TweetQueue.objects.filter(
        event_detail=instance, tweet_type="slide_share",
    ).order_by('created_at', 'pk').first()
    if existing_queue:
        sync_slide_share_queue_image(instance)
        return

    queue_item = TweetQueue.objects.create(
        tweet_type="slide_share",
        community=instance.event.community,
        event=instance.event,
        event_detail=instance,
        scheduled_at=default_scheduled_at(tweet_type='slide_share', event=instance.event),
    )
    image_url = get_tweet_image_url(queue_item)
    if image_url:
        queue_item.image_url = image_url
        queue_item.save(update_fields=['image_url'])
    logger.info(
        "Queued slide share tweet: %s - %s", instance.speaker, instance.theme,
    )

    _start_tweet_generation(queue_item)


@receiver(post_save, sender=EventDetail)
def queue_event_detail_tweet(sender, instance, created, **kwargs):
    """LT/特別回の EventDetail に応じてキューを更新する。"""
    try:
        _queue_event_detail_tweet(instance, created)
    except Exception:
        logger.exception("Failed to queue event detail tweet for EventDetail %s", instance.pk)


def _queue_event_detail_tweet(instance, created):
    from twitter.models import TweetQueue

    if instance.detail_type not in PRESENTATION_DETAIL_TYPES:
        _sync_daily_reminders_for_instance(instance, created)
        return

    if instance.status != "approved":
        _sync_daily_reminders_for_instance(instance, created)
        return

    if instance.event.date < timezone.localdate():
        _sync_daily_reminders_for_instance(instance, created)
        return

    old_status = getattr(instance, "_old_status", None)
    tweet_type = "lt" if instance.detail_type == "LT" else "special"

    if instance.event.date == timezone.localdate():
        _ensure_same_day_individual_queue_skipped(instance, tweet_type)
        _sync_daily_reminders_for_instance(instance, created)
        return

    if not created and old_status == "approved":
        old_speaker = getattr(instance, "_old_speaker", "")
        old_theme = getattr(instance, "_old_theme", "")
        new_speaker = instance.speaker or ""
        new_theme = instance.theme or ""

        if old_speaker == new_speaker and old_theme == new_theme:
            _sync_daily_reminders_for_instance(instance, created)
            return

        deleted, _ = TweetQueue.objects.filter(
            event_detail=instance,
            tweet_type=tweet_type,
            status__in=('generating', 'generation_failed', 'ready'),
        ).delete()
        if deleted:
            logger.info("Deleted %d unposted %s tweet(s) for regeneration", deleted, tweet_type)

    else:
        if TweetQueue.objects.filter(event_detail=instance, tweet_type=tweet_type).exists():
            _sync_daily_reminders_for_instance(instance, created)
            return

    queue_item = TweetQueue.objects.create(
        tweet_type=tweet_type,
        community=instance.event.community,
        event=instance.event,
        event_detail=instance,
        scheduled_at=default_scheduled_at(tweet_type=tweet_type, event=instance.event),
    )
    logger.info("Queued %s tweet: %s - %s", tweet_type, instance.speaker, instance.theme)

    _sync_daily_reminders_for_instance(instance, created)

    _start_tweet_generation(queue_item)


@receiver(post_delete, sender=EventDetail)
def sync_daily_reminder_on_event_detail_delete(sender, instance, **kwargs):
    """当日発表の削除後に daily_reminder を同期する。"""
    try:
        if _is_active_presentation(instance.detail_type, instance.event.date):
            _sync_daily_reminder_for_event(instance.event_id)
    except Exception:
        logger.exception("Failed to sync daily reminder after EventDetail delete %s", instance.pk)
