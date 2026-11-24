from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.utils import timezone

DEFAULT_POST_HOUR = 19
TWEET_TYPE_POST_HOURS = {
    'slide_share': 10,
    'new_community': 12,
    'lt': 12,
    'special': 12,
    'daily_reminder': DEFAULT_POST_HOUR,
}


def _current_tz():
    return timezone.get_current_timezone()


def _ensure_aware(value: datetime) -> datetime:
    if timezone.is_naive(value):
        return timezone.make_aware(value, _current_tz())
    return value


def scheduled_at_for_date(target_date: date, hour: int = DEFAULT_POST_HOUR) -> datetime:
    naive = datetime.combine(target_date, time(hour=hour))
    return timezone.make_aware(naive, _current_tz())


def default_scheduled_at(tweet_type: str, event=None, base_datetime: datetime | None = None) -> datetime:
    if tweet_type == 'daily_reminder' and event is not None:
        return scheduled_at_for_date(event.date)

    post_hour = TWEET_TYPE_POST_HOURS.get(tweet_type, DEFAULT_POST_HOUR)
    base = _ensure_aware(base_datetime or timezone.now())
    local_base = timezone.localtime(base, _current_tz())
    scheduled = scheduled_at_for_date(local_base.date(), hour=post_hour)
    if local_base >= scheduled:
        # 当日 12:00 以降の承認は翌日に送らず即時キューへ乗せる（発表開始前の判定は signal 側）
        if event is not None and tweet_type in ('lt', 'special') and event.date == local_base.date():
            return local_base
        scheduled += timedelta(days=1)

    if event is not None and tweet_type in ('lt', 'special'):
        raw_start = getattr(event, 'start_time', None)
        start_time = (
            raw_start
            if isinstance(raw_start, time)
            else (time.fromisoformat(raw_start) if isinstance(raw_start, str) else None)
        )
        if start_time is not None:
            event_start = timezone.make_aware(
                datetime.combine(event.date, start_time),
                _current_tz(),
            )
            if scheduled >= event_start and local_base < event_start:
                if event.date == local_base.date():
                    return local_base
                return max(local_base, event_start - timedelta(hours=2))
    return scheduled
