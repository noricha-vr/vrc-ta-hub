"""ユーザー起点の定期イベント削除・日付移動を保持する。"""

from collections.abc import Iterable
from datetime import date, datetime, time

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from community.constants import weekday_code
from event.models import Event, EventDetail, EventOccurrenceTombstone
from twitter.services.announcement_eligibility import (
    MOVED_PAST_START_SKIP_REASON,
    PRESERVED_MISSKEY_STATUSES,
    RETRACTED_PRESENTATION_SKIP_MESSAGE,
    presentation_detail_skip_reason,
    revocable_unposted_queue_q,
)


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
        event.weekday = weekday_code(new_date)
        update_fields = ['date', 'weekday']
        if event.recurring_master_id is not None:
            event.recurring_master = None
            update_fields.append('recurring_master')
        event.save(update_fields=update_fields)
        _sync_tweet_queues_for_moved_event(event)
        transaction.on_commit(
            lambda: invalidate_event_date_caches([event_id]),
            robust=True,
        )
    return event


def sync_tweet_queues_for_event_schedule_change(
    event: Event,
    *,
    regenerate_reason: str,
) -> None:
    """イベント開催日・開始時刻変更時に未投稿キューの予約・再生成・Misskey 状態を同期する。"""
    from twitter.models import MisskeyNote, TweetQueue
    from twitter.scheduling import default_scheduled_at

    now_ts = timezone.now()
    daily_reminder_qs = TweetQueue.objects.filter(
        event=event,
        tweet_type='daily_reminder',
    ).exclude(status__in=('posted', 'skipped'))
    daily_reminder_ids = list(daily_reminder_qs.values_list('pk', flat=True))
    if daily_reminder_ids:
        TweetQueue.objects.filter(pk__in=daily_reminder_ids).update(
            status='skipped',
            generated_text='',
            error_message='当日リマインドは廃止されたため投稿スキップ',
            generation_token='',
        )
        MisskeyNote.objects.filter(
            tweet_queue_id__in=daily_reminder_ids,
            status='pending',
        ).update(
            status='skipped',
            error_message='当日リマインドは廃止されたため投稿スキップ',
            updated_at=now_ts,
        )

    TweetQueue.objects.filter(
        event=event,
        tweet_type='new_community',
        status__in=('generating', 'generation_failed', 'ready'),
    ).update(
        status='generation_failed',
        generated_text='',
        error_message=regenerate_reason,
        generation_token='',
    )

    presentation_queues = TweetQueue.objects.filter(
        event=event,
        tweet_type__in=('lt', 'special'),
    ).filter(
        revocable_unposted_queue_q(),
    ).select_related('event_detail')
    local_now = timezone.localtime(now_ts)
    raw_start = event.start_time
    start_time = (
        raw_start
        if isinstance(raw_start, time)
        else time.fromisoformat(raw_start)
    )
    event_start = timezone.make_aware(
        datetime.combine(event.date, start_time),
        timezone.get_current_timezone(),
    )

    if event.date < local_now.date() or event_start <= local_now:
        skipped_ids = list(presentation_queues.values_list('pk', flat=True))
        if skipped_ids:
            TweetQueue.objects.filter(pk__in=skipped_ids).update(
                status='skipped',
                generated_text='',
                error_message=MOVED_PAST_START_SKIP_REASON,
                generation_token='',
            )
            MisskeyNote.objects.filter(
                tweet_queue_id__in=skipped_ids,
                status='pending',
            ).update(
                status='skipped',
                error_message=MOVED_PAST_START_SKIP_REASON,
                updated_at=now_ts,
            )
        return

    delivered_detail_keys = set(
        TweetQueue.objects.filter(
            event=event,
            tweet_type__in=('lt', 'special'),
            event_detail_id__isnull=False,
            status='posted',
        ).values_list('event_detail_id', 'tweet_type')
    )
    preserved_mk_pks_by_key: dict[tuple[int, str], set[int]] = {}
    for pk, detail_id, t_type in TweetQueue.objects.filter(
        event=event,
        tweet_type__in=('lt', 'special'),
        event_detail_id__isnull=False,
        misskey_note__status__in=PRESERVED_MISSKEY_STATUSES,
    ).values_list('pk', 'event_detail_id', 'tweet_type'):
        preserved_mk_pks_by_key.setdefault((detail_id, t_type), set()).add(pk)

    candidates = list(presentation_queues)
    candidates.sort(
        key=lambda q: (
            0 if q.status in ('generating', 'generation_failed', 'ready') else 1,
            0
            if (
                q.event_detail_id is not None
                and q.pk in preserved_mk_pks_by_key.get((q.event_detail_id, q.tweet_type), set())
            )
            else 1,
            -q.pk,
        )
    )

    ineligible_ids: list[int] = []
    revived_ids: list[int] = []
    active_detail_keys: set[tuple[int, str]] = set()
    for queue in candidates:
        if presentation_detail_skip_reason(queue):
            queue.status = 'skipped'
            queue.generated_text = ''
            queue.error_message = RETRACTED_PRESENTATION_SKIP_MESSAGE
            queue.generation_token = ''
            queue.save(
                update_fields=[
                    'status',
                    'generated_text',
                    'error_message',
                    'generation_token',
                ],
            )
            ineligible_ids.append(queue.pk)
            continue

        if queue.event_detail_id is not None:
            key = (queue.event_detail_id, queue.tweet_type)
            if queue.status in ('generating', 'generation_failed', 'ready'):
                if key in active_detail_keys:
                    queue.status = 'skipped'
                    queue.generated_text = ''
                    queue.error_message = '重複告知を防ぐためスキップ'
                    queue.generation_token = ''
                    queue.save(
                        update_fields=[
                            'status',
                            'generated_text',
                            'error_message',
                            'generation_token',
                        ],
                    )
                    ineligible_ids.append(queue.pk)
                    continue
                active_detail_keys.add(key)
            else:
                has_other_preserved_mk = bool(
                    preserved_mk_pks_by_key.get(key, set()) - {queue.pk}
                )
                if (
                    key in active_detail_keys
                    or key in delivered_detail_keys
                    or has_other_preserved_mk
                ):
                    continue
                active_detail_keys.add(key)

        queue.scheduled_at = default_scheduled_at(queue.tweet_type, event=event)
        queue.status = 'generation_failed'
        queue.generated_text = ''
        queue.error_message = regenerate_reason
        queue.generation_token = ''
        queue.save(
            update_fields=[
                'scheduled_at',
                'status',
                'generated_text',
                'error_message',
                'generation_token',
            ],
        )
        revived_ids.append(queue.pk)

    if ineligible_ids:
        MisskeyNote.objects.filter(
            tweet_queue_id__in=ineligible_ids,
            status='pending',
        ).update(
            status='skipped',
            error_message=RETRACTED_PRESENTATION_SKIP_MESSAGE,
            updated_at=now_ts,
        )
    if revived_ids:
        MisskeyNote.objects.filter(
            tweet_queue_id__in=revived_ids,
            status='skipped',
        ).update(
            status='pending',
            error_message='',
            attempts=0,
            updated_at=now_ts,
        )

    approved_details = EventDetail.objects.filter(
        event=event,
        detail_type__in=('LT', 'SPECIAL'),
        status='approved',
        deleted_at__isnull=True,
    )
    for detail in approved_details:
        tweet_type = 'lt' if detail.detail_type == 'LT' else 'special'
        if TweetQueue.objects.filter(event_detail=detail, tweet_type=tweet_type).exists():
            continue
        TweetQueue.objects.create(
            tweet_type=tweet_type,
            community=event.community,
            event=event,
            event_detail=detail,
            scheduled_at=default_scheduled_at(tweet_type, event=event),
            status='generation_failed',
            generated_text='',
            error_message=regenerate_reason,
            generation_token='',
        )


def _sync_tweet_queues_for_moved_event(event: Event) -> None:
    """開催日変更時に廃止済みリマインドをスキップし、未投稿の発表告知を再予約・再生成待ちにする。"""
    sync_tweet_queues_for_event_schedule_change(
        event,
        regenerate_reason='開催日変更により再生成',
    )


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
