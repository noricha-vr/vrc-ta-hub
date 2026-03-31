from datetime import datetime, time

from vket.models import VketParticipation


EVENT_DETAIL_DATETIME_LOCK_MESSAGE = "Vketコラボ期間中のため運営のみ変更できます。"


def is_event_datetime_locked(event, user) -> bool:
    if getattr(user, "is_superuser", False):
        return False

    if not event.community_id or not event.date:
        return False

    return VketParticipation.objects.filter(
        community_id=event.community_id,
        lifecycle=VketParticipation.Lifecycle.ACTIVE,
        collaboration__period_start__lte=event.date,
        collaboration__period_end__gte=event.date,
    ).exists()


def is_event_detail_datetime_locked(event_detail, user) -> bool:
    return is_event_datetime_locked(event_detail.event, user)


def has_event_detail_start_time_changed(event_detail, start_time_value) -> bool:
    parsed_start_time = _parse_time_value(start_time_value)
    if parsed_start_time is None:
        return False
    return parsed_start_time != event_detail.start_time


def has_event_detail_duration_changed(event_detail, duration_value) -> bool:
    parsed_duration = _parse_duration_value(duration_value)
    if parsed_duration is None:
        return False
    return parsed_duration != event_detail.duration


def _parse_time_value(value):
    if value in (None, ""):
        return None
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    if isinstance(value, str):
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                return datetime.strptime(value, fmt).time()
            except ValueError:
                continue
    return value


def _parse_duration_value(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value
