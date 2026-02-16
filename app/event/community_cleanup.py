from datetime import date, datetime, timedelta

from django.conf import settings
from googleapiclient.errors import HttpError

from community.models import Community
from event.google_calendar import GoogleCalendarService
from event.models import Event, RecurrenceRule


def cleanup_community_future_data(
    community,
    from_date: date,
    *,
    delete_rules: bool = True,
    delete_google_events: bool = True,
    google_window_days: int = 180,
    google_years: int = 3,
):
    """指定コミュニティの指定日以降データをクリーンアップする。"""
    stats = {
        "db_events": 0,
        "rules": 0,
        "google_events": 0,
    }

    target_events = list(
        Event.objects.filter(
            community=community,
            date__gte=from_date,
        )
    )
    event_ids = [event.id for event in target_events]
    stats["db_events"] = len(event_ids)
    if event_ids:
        Event.objects.filter(id__in=event_ids).delete()

    if delete_rules:
        rules = list(RecurrenceRule.objects.filter(community=community))
        for rule in rules:
            rule.delete_future_events(from_date)
            rule.delete(delete_future_events=False)
        stats["rules"] = len(rules)

    if delete_google_events:
        service = GoogleCalendarService(
            calendar_id=settings.GOOGLE_CALENDAR_ID,
            credentials_path=settings.GOOGLE_CALENDAR_CREDENTIALS,
        )
        # まずDBに残っていたGoogle Calendar IDを優先して削除する
        existing_google_ids = {
            event.google_calendar_event_id for event in target_events if event.google_calendar_event_id
        }
        deleted_by_ids = _delete_google_events_by_ids(
            service=service,
            google_event_ids=existing_google_ids,
        )
        deleted_by_summary = 0

        # 直接DB操作などでIDが消えている残骸は、同名が一意な場合のみ補完削除する
        is_name_unique = Community.objects.filter(name=community.name).count() == 1
        if is_name_unique:
            deleted_by_summary = _delete_google_events_by_summary(
                service=service,
                community_name=community.name,
                from_date=from_date,
                window_days=google_window_days,
                years=google_years,
                already_deleted_ids=existing_google_ids,
            )

        stats["google_events"] = deleted_by_ids + deleted_by_summary

    return stats


def _delete_google_events_by_ids(
    service: GoogleCalendarService,
    google_event_ids: set[str],
) -> int:
    deleted = 0
    for event_id in google_event_ids:
        try:
            service.delete_event(event_id)
            deleted += 1
        except HttpError as e:
            # 既に削除済み(404)は再実行時の正常ケースとして扱う
            if getattr(e, "status_code", None) == 404 or (hasattr(e, "resp") and getattr(e.resp, "status", None) == 404):
                continue
            raise
    return deleted


def _delete_google_events_by_summary(
    service: GoogleCalendarService,
    community_name: str,
    from_date: date,
    *,
    window_days: int,
    years: int,
    already_deleted_ids: set[str],
) -> int:
    start = datetime.combine(from_date, datetime.min.time())
    hard_end_date = from_date + timedelta(days=365 * years)
    end = datetime.combine(hard_end_date, datetime.max.time())

    matched_ids = set()
    current = start
    while current < end:
        window_end = min(current + timedelta(days=window_days), end)
        events = service.list_events(
            time_min=current,
            time_max=window_end,
            max_results=2500,
        )
        for event in events:
            summary = (event.get("summary") or "").strip()
            if summary != community_name:
                continue
            event_date = _extract_event_date(event)
            if event_date and event_date >= from_date:
                matched_ids.add(event["id"])
        current = window_end

    deleted = 0
    for event_id in matched_ids:
        if event_id in already_deleted_ids:
            continue
        try:
            service.delete_event(event_id)
            deleted += 1
        except HttpError as e:
            if getattr(e, "status_code", None) == 404 or (hasattr(e, "resp") and getattr(e.resp, "status", None) == 404):
                continue
            raise
    return deleted


def _extract_event_date(event) -> date | None:
    start = event.get("start", {})
    if "dateTime" in start:
        try:
            dt = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
            return dt.date()
        except ValueError:
            return None
    if "date" in start:
        try:
            return date.fromisoformat(start["date"])
        except ValueError:
            return None
    return None
