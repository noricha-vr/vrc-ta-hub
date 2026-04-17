from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.utils import timezone

DEFAULT_POST_HOUR = 19


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

    base = _ensure_aware(base_datetime or timezone.now())
    local_base = timezone.localtime(base, _current_tz())
    scheduled = scheduled_at_for_date(local_base.date())
    if local_base >= scheduled:
        scheduled += timedelta(days=1)
    return scheduled
