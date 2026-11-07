"""X と Misskey で共有する投稿対象判定。"""

from django.utils import timezone

UNLISTED_COMMUNITY_SKIP_MESSAGE = '集会一覧に載せない集会のため新集会の告知は投稿しません'


def unlisted_community_skip_reason(queue_item) -> str | None:
    """集会一覧に載せない集会の新集会告知を除外する理由を返す。

    承認時に積んだ後で非掲載にした分も止めるため、送信の直前に今の設定を見る。
    """
    community = queue_item.community
    if queue_item.tweet_type == 'new_community' and community is not None and not community.is_listed:
        return 'unlisted_community'
    return None


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
