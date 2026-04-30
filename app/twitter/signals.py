"""Django シグナル: 集会承認・LT/特別回承認時に TweetQueue へ自動追加。

当日開催の発表は個別告知をスキップ扱いにし、同時に daily_reminder を同期する。
"""

import logging
import threading

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from community.models import Community
from event.models import EventDetail
from twitter.scheduling import default_scheduled_at

logger = logging.getLogger(__name__)

PRESENTATION_DETAIL_TYPES = ("LT", "SPECIAL")
SAME_DAY_INDIVIDUAL_SKIP_REASON = '当日リマインドに統合したため個別告知は投稿しません'
NO_APPROVED_PRESENTATIONS_SKIP_REASON = '承認済みの当日発表がないため投稿対象外'


def _generate_tweet_async(queue_id: int) -> None:
    """バックグラウンドスレッドでツイートテキストを生成する。"""
    from django.db import connections

    try:
        from twitter.models import TweetQueue
        from twitter.tweet_generator import get_generator, get_tweet_image_url

        try:
            queue_item = TweetQueue.objects.select_related(
                'community', 'event', 'event_detail',
            ).get(pk=queue_id)
        except TweetQueue.DoesNotExist:
            logger.error("TweetQueue %d not found", queue_id)
            return

        generator = get_generator(queue_item.tweet_type)
        text = generator(queue_item) if generator else None

        if not text:
            queue_item.status = 'generation_failed'
            queue_item.error_message = 'テキスト生成に失敗'
            queue_item.save()
            return

        queue_item.generated_text = text

        image_url = get_tweet_image_url(queue_item)
        if image_url:
            queue_item.image_url = image_url

        queue_item.status = 'ready'
        queue_item.error_message = ''
        queue_item.save()
        logger.info("Tweet text generated for queue %d", queue_id)

    except Exception as e:
        logger.warning(
            "Async tweet generation failed for queue %d: %s", queue_id, e,
        )
        try:
            from twitter.models import TweetQueue

            item = TweetQueue.objects.get(pk=queue_id)
            item.status = 'generation_failed'
            item.error_message = str(e)[:500]
            item.save()
        except Exception:
            pass
    finally:
        connections.close_all()


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
            pass


def _is_active_presentation(detail_type, event_date) -> bool:
    return detail_type in PRESENTATION_DETAIL_TYPES and event_date >= timezone.localdate()


def _should_refresh_daily_reminder(instance, created: bool) -> bool:
    if created:
        return True

    return any((
        getattr(instance, "_old_status", None) != instance.status,
        getattr(instance, "_old_speaker", "") != (instance.speaker or ""),
        getattr(instance, "_old_theme", "") != (instance.theme or ""),
        getattr(instance, "_old_start_time", None) != instance.start_time,
        getattr(instance, "_old_detail_type", None) != instance.detail_type,
        getattr(instance, "_old_event_id", None) != instance.event_id,
    ))


def _iter_event_ids_to_sync(instance):
    event_ids = set()

    if _is_active_presentation(instance.detail_type, instance.event.date):
        event_ids.add(instance.event_id)

    old_detail_type = getattr(instance, "_old_detail_type", None)
    old_event_id = getattr(instance, "_old_event_id", None)
    old_event_date = getattr(instance, "_old_event_date", None)
    if old_event_id and _is_active_presentation(old_detail_type, old_event_date):
        event_ids.add(old_event_id)

    return sorted(event_ids)


def _ensure_same_day_individual_queue_skipped(instance, tweet_type: str) -> None:
    from twitter.models import TweetQueue

    existing_qs = TweetQueue.objects.filter(
        event_detail=instance, tweet_type=tweet_type,
    ).order_by('created_at', 'pk')
    primary = existing_qs.first()

    if primary is None:
        TweetQueue.objects.create(
            tweet_type=tweet_type,
            community=instance.event.community,
            event=instance.event,
            event_detail=instance,
            scheduled_at=default_scheduled_at(tweet_type=tweet_type, event=instance.event),
            status='skipped',
            error_message=SAME_DAY_INDIVIDUAL_SKIP_REASON,
        )
        logger.info(
            "Queued skipped same-day %s tweet: %s - %s",
            tweet_type,
            instance.speaker,
            instance.theme,
        )
        return

    update_fields = []
    if primary.community_id != instance.event.community_id:
        primary.community = instance.event.community
        update_fields.append('community')
    if primary.event_id != instance.event_id:
        primary.event = instance.event
        update_fields.append('event')
    scheduled_at = default_scheduled_at(tweet_type=tweet_type, event=instance.event)
    if primary.scheduled_at != scheduled_at:
        primary.scheduled_at = scheduled_at
        update_fields.append('scheduled_at')
    if primary.status != 'posted' and primary.status != 'skipped':
        primary.status = 'skipped'
        update_fields.append('status')
    if primary.error_message != SAME_DAY_INDIVIDUAL_SKIP_REASON:
        primary.error_message = SAME_DAY_INDIVIDUAL_SKIP_REASON
        update_fields.append('error_message')
    if primary.generated_text:
        primary.generated_text = ''
        update_fields.append('generated_text')

    if update_fields:
        primary.save(update_fields=update_fields)

    existing_qs.exclude(pk=primary.pk).exclude(status='posted').delete()


def _sync_daily_reminder_for_event(event_id: int) -> None:
    from event.models import Event
    from twitter.models import TweetQueue
    from twitter.views import _retry_generation

    try:
        event = Event.objects.select_related('community').get(pk=event_id)
    except Event.DoesNotExist:
        return

    if event.date < timezone.localdate():
        return

    queue = TweetQueue.objects.filter(
        event=event, tweet_type='daily_reminder',
    ).first()
    has_presentations = event.details.filter(
        status='approved', detail_type__in=PRESENTATION_DETAIL_TYPES,
    ).exists()

    if not has_presentations:
        if queue and queue.status != 'posted':
            queue.status = 'skipped'
            queue.error_message = NO_APPROVED_PRESENTATIONS_SKIP_REASON
            queue.generated_text = ''
            queue.save(update_fields=['status', 'error_message', 'generated_text'])
            logger.info(
                "Skipped daily reminder tweet for event %d because no approved presentations remain",
                event.pk,
            )
        return

    if queue and queue.status == 'posted':
        return

    if queue is None:
        queue = TweetQueue.objects.create(
            tweet_type='daily_reminder',
            community=event.community,
            event=event,
            scheduled_at=default_scheduled_at(tweet_type='daily_reminder', event=event),
            status='generating',
        )
    else:
        queue.community = event.community
        queue.scheduled_at = default_scheduled_at(tweet_type='daily_reminder', event=event)
        queue.status = 'generating'
        queue.error_message = ''
        queue.generated_text = ''
        queue.save(update_fields=['community', 'scheduled_at', 'status', 'error_message', 'generated_text'])

    _retry_generation(queue)
    logger.info("Synced daily reminder tweet for event %d", event.pk)


def _sync_daily_reminders_for_instance(instance, created: bool) -> None:
    if not _should_refresh_daily_reminder(instance, created):
        return

    for event_id in _iter_event_ids_to_sync(instance):
        _sync_daily_reminder_for_event(event_id)


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

    thread = threading.Thread(
        target=_generate_tweet_async, args=(queue_item.pk,), daemon=True,
    )
    thread.start()


@receiver(post_save, sender=EventDetail)
def queue_slide_share_tweet(sender, instance, created, **kwargs):
    """スライド/記事が初めてアップロードされた時にツイートキューに追加する。"""
    try:
        _queue_slide_share_tweet(instance, created)
    except Exception:
        logger.exception("Failed to queue slide share tweet for EventDetail %s", instance.pk)


def _queue_slide_share_tweet(instance, created):
    from twitter.models import TweetQueue

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

    if TweetQueue.objects.filter(
        event_detail=instance, tweet_type="slide_share",
    ).exists():
        return

    queue_item = TweetQueue.objects.create(
        tweet_type="slide_share",
        community=instance.event.community,
        event=instance.event,
        event_detail=instance,
        scheduled_at=default_scheduled_at(tweet_type='slide_share', event=instance.event),
    )
    logger.info(
        "Queued slide share tweet: %s - %s", instance.speaker, instance.theme,
    )

    thread = threading.Thread(
        target=_generate_tweet_async, args=(queue_item.pk,), daemon=True,
    )
    thread.start()


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

    thread = threading.Thread(
        target=_generate_tweet_async, args=(queue_item.pk,), daemon=True,
    )
    thread.start()


@receiver(post_delete, sender=EventDetail)
def sync_daily_reminder_on_event_detail_delete(sender, instance, **kwargs):
    """当日発表の削除後に daily_reminder を同期する。"""
    try:
        if _is_active_presentation(instance.detail_type, instance.event.date):
            _sync_daily_reminder_for_event(instance.event_id)
    except Exception:
        logger.exception("Failed to sync daily reminder after EventDetail delete %s", instance.pk)
