"""X と Misskey で共有する投稿対象判定。"""

from datetime import time

from django.db.models import Q
from django.utils import timezone

UNLISTED_COMMUNITY_SKIP_MESSAGE = '集会一覧に載せない集会のため新集会の告知は投稿しません'
EVENT_STARTED_SKIP_MESSAGE = 'イベント開始時刻を過ぎたため投稿スキップ'
EVENT_DATE_PASSED_SKIP_MESSAGE = 'イベント日が過去のため投稿スキップ'
SCHEDULE_EXPIRED_SKIP_REASON = '予約日時から24時間以上経過したため投稿をスキップ'
MOVED_PAST_START_SKIP_REASON = 'イベント日付の移動後、開始時刻を過ぎているためスキップ'
LEGACY_SAME_DAY_INDIVIDUAL_SKIP_REASON = '当日リマインドに統合したため個別告知は投稿しません'
REVOCABLE_SKIPPED_MESSAGES = (
    MOVED_PAST_START_SKIP_REASON,
    EVENT_STARTED_SKIP_MESSAGE,
    SCHEDULE_EXPIRED_SKIP_REASON,
    LEGACY_SAME_DAY_INDIVIDUAL_SKIP_REASON,
)
REVOCABLE_FAILED_MESSAGES = (EVENT_DATE_PASSED_SKIP_MESSAGE,)
REVOCABLE_STARTED_SKIP_MESSAGES = REVOCABLE_SKIPPED_MESSAGES
PRESERVED_MISSKEY_STATUSES = ('posted', 'sending', 'unknown')
RETRACTED_PRESENTATION_SKIP_MESSAGE = '発表が取り下げまたは変更されたため投稿スキップ'
PRESENTATION_QUEUE_DETAIL_TYPES = {
    'lt': 'LT',
    'special': 'SPECIAL',
}


def revocable_unposted_queue_q() -> Q:
    """日時都合・旧統合による未送信を含む、再予約・取消対象の TweetQueue 条件を返す。"""
    return (
        Q(status__in=('generating', 'generation_failed', 'ready'))
        | Q(status='skipped', error_message__in=REVOCABLE_SKIPPED_MESSAGES)
        | Q(status='failed', error_message__in=REVOCABLE_FAILED_MESSAGES)
    )


def _coerce_time(value) -> time | None:
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        try:
            return time.fromisoformat(value)
        except ValueError:
            return None
    return None


def unlisted_community_skip_reason(queue_item) -> str | None:
    """集会一覧に載せない集会の新集会告知を除外する理由を返す。

    承認時に積んだ後で非掲載にした分も止めるため、送信の直前に今の設定を見る。
    """
    community = queue_item.community
    if queue_item.tweet_type == 'new_community' and community is not None and not community.is_listed:
        return 'unlisted_community'
    return None


def event_date_skip_reason(queue_item, *, today=None, now=None) -> str | None:
    """投稿先の状態には触れず、イベント日時による除外理由だけを返す。"""
    if queue_item.tweet_type == 'daily_reminder':
        return 'daily_reminder_retired'
    if not queue_item.event:
        return None
    local_now = timezone.localtime(now or timezone.now())
    today = today if today is not None else local_now.date()
    event_date = queue_item.event.date
    if queue_item.tweet_type in ('lt', 'special'):
        if event_date < today:
            return 'event_date_passed'
        if event_date == today and local_now.date() == event_date:
            start_time = _coerce_time(getattr(queue_item.event, 'start_time', None))
            if start_time is not None and local_now.time() >= start_time:
                return 'event_started'
    return None


def presentation_detail_skip_reason(queue_item) -> str | None:
    """lt/special キューの発表が承認済み・未削除・一致種別か確認する。"""
    expected_detail_type = PRESENTATION_QUEUE_DETAIL_TYPES.get(queue_item.tweet_type)
    if not expected_detail_type:
        return None
    detail = queue_item.event_detail
    if detail is None:
        return None
    if queue_item.event_id is not None and detail.event_id != queue_item.event_id:
        return 'detail_event_mismatch'
    if getattr(detail, 'deleted_at', None) is not None:
        return 'detail_deleted'
    if detail.status != 'approved':
        return 'detail_not_approved'
    if detail.detail_type != expected_detail_type:
        return 'detail_type_mismatch'
    return None
