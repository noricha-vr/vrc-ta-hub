"""X と Misskey で共有するイベント日の投稿対象判定。"""

from django.utils import timezone


def event_date_skip_reason(queue_item, *, today=None) -> str | None:
    """投稿先の状態には触れず、イベント日による除外理由だけを返す。"""
    if not queue_item.event:
        return None
    today = today if today is not None else timezone.localdate()
    event_date = queue_item.event.date
    if queue_item.tweet_type in ('lt', 'special'):
        if event_date == today:
            return 'same_day_integrated'
        if event_date < today:
            return 'event_date_passed'
    if queue_item.tweet_type == 'daily_reminder' and event_date != today:
        return 'not_event_day'
    return None
