"""ユーザー起点の定期イベント削除・日付移動を保持する。"""

from collections.abc import Iterable
from datetime import date

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from event.models import Event, EventOccurrenceTombstone


def upsert_occurrence_tombstone(
    event: Event,
    reason: str,
) -> EventOccurrenceTombstone:
    """イベントの元開催日を再生成除外として保存する。"""
    tombstone, _ = EventOccurrenceTombstone.objects.update_or_create(
        community=event.community,
        date=event.date,
        defaults={
            'original_start_time': event.start_time,
            'reason': reason,
        },
    )
    return tombstone


def exclude_tombstoned_dates(community, dates: Iterable[date]) -> list[date]:
    """集会の tombstone に一致する日付を候補から除外する。"""
    date_list = list(dates)
    if not date_list:
        return []
    excluded = set(
        EventOccurrenceTombstone.objects.filter(
            community=community,
            date__in=date_list,
        ).values_list('date', flat=True)
    )
    return [candidate for candidate in date_list if candidate not in excluded]


def get_cascade_occurrences(event: Event) -> list[Event]:
    """イベント削除で CASCADE 対象になる開催回も含めて返す。"""
    occurrences = [event]
    if event.is_recurring_master:
        occurrences.extend(
            event.recurring_instances.select_related('community').order_by(
                'date', 'start_time', 'pk'
            )
        )
    return occurrences


def delete_event_with_tombstones(
    event: Event,
    occurrences: Iterable[Event] | None = None,
) -> int:
    """対象開催回の tombstone を記録してイベントを削除する。"""
    occurrence_list = list(occurrences or get_cascade_occurrences(event))
    event_ids = [occurrence.pk for occurrence in occurrence_list]
    with transaction.atomic():
        for occurrence in occurrence_list:
            upsert_occurrence_tombstone(
                occurrence,
                EventOccurrenceTombstone.Reason.DELETED,
            )
        event.delete()
        transaction.on_commit(
            lambda: invalidate_event_date_caches(event_ids),
            robust=True,
        )
    return len(occurrence_list)


def move_event_occurrence(event: Event, new_date: date) -> Event:
    """元日を tombstone 化し、イベントと未投稿当日リマインドを移動する。"""
    event_id = event.pk
    with transaction.atomic():
        upsert_occurrence_tombstone(
            event,
            EventOccurrenceTombstone.Reason.RESCHEDULED,
        )
        event.date = new_date
        event.weekday = new_date.strftime('%a')
        update_fields = ['date', 'weekday']
        if event.recurring_master_id is not None:
            event.recurring_master = None
            update_fields.append('recurring_master')
        event.save(update_fields=update_fields)
        _reschedule_daily_reminders(event)
        transaction.on_commit(
            lambda: invalidate_event_date_caches([event_id]),
            robust=True,
        )
    return event


def _reschedule_daily_reminders(event: Event) -> None:
    """未投稿の当日リマインドを新しい開催日に合わせる。"""
    from twitter.models import TweetQueue
    from twitter.scheduling import scheduled_at_for_date

    scheduled_at = scheduled_at_for_date(event.date)
    queues = TweetQueue.objects.filter(
        event=event,
        tweet_type='daily_reminder',
    ).exclude(status='posted')
    for queue in queues:
        queue.scheduled_at = scheduled_at
        update_fields = ['scheduled_at']
        if scheduled_at <= timezone.now():
            queue.status = 'skipped'
            queue.error_message = 'イベント日付の移動後、予約日時が過去のためスキップ'
            update_fields.extend(['status', 'error_message'])
        queue.save(update_fields=update_fields)


def invalidate_event_date_caches(event_ids: Iterable[int]) -> None:
    """日付由来のトップ・カレンダーURLキャッシュを破棄する。"""
    from event_calendar.calendar_utils import generate_google_calendar_url
    from ta_hub.index_cache import clear_index_view_cache

    clear_index_view_cache()
    for event_id in event_ids:
        cache.delete_many([
            f'google_calendar_url_{event_id}',
            f'calendar_entry_url_{event_id}',
            f'calendar_entry_url_{event_id}_False',
            f'calendar_entry_url_{event_id}_True',
        ])
    generate_google_calendar_url.cache_clear()
