"""当日リマインド（daily_reminder）TweetQueue の同期ヘルパー。

EventDetail の変更・削除を受けて、当日発表を束ねた daily_reminder キューと
個別告知キューのスキップ状態を整合させる。
"""

import logging

from django.utils import timezone

from twitter.scheduling import default_scheduled_at
from twitter.services import tweet_generation

logger = logging.getLogger(__name__)

PRESENTATION_DETAIL_TYPES = ("LT", "SPECIAL")
SAME_DAY_INDIVIDUAL_SKIP_REASON = '当日リマインドに統合したため個別告知は投稿しません'
NO_APPROVED_PRESENTATIONS_SKIP_REASON = '承認済みの当日発表がないため投稿対象外'


def is_active_presentation(detail_type, event_date) -> bool:
    """発表が当日リマインドの同期対象か判定する。"""
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

    if is_active_presentation(instance.detail_type, instance.event.date):
        event_ids.add(instance.event_id)

    old_detail_type = getattr(instance, "_old_detail_type", None)
    old_event_id = getattr(instance, "_old_event_id", None)
    old_event_date = getattr(instance, "_old_event_date", None)
    if old_event_id and is_active_presentation(old_detail_type, old_event_date):
        event_ids.add(old_event_id)

    return sorted(event_ids)


def ensure_same_day_individual_queue_skipped(instance, tweet_type: str) -> None:
    """同日開催の個別告知キューをスキップ状態に揃える。"""
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


def sync_daily_reminder_for_event(event_id: int) -> None:
    """指定イベントの当日リマインドキューを同期する。"""
    from event.models import Event
    from twitter.models import TweetQueue

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

    tweet_generation.start_tweet_generation(queue)
    logger.info("Synced daily reminder tweet for event %d", event.pk)


def sync_daily_reminders_for_instance(instance, created: bool) -> None:
    """EventDetail の変更に応じて当日リマインドを同期する。"""
    if not _should_refresh_daily_reminder(instance, created):
        return

    for event_id in _iter_event_ids_to_sync(instance):
        sync_daily_reminder_for_event(event_id)
