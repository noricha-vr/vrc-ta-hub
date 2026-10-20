"""画面と API で共用する発表申請の承認・却下処理。"""

import logging
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404

from event.models import EventDetail
from vket.application_link import application_warnings, link_approved_application, lock_application_event

logger = logging.getLogger(__name__)


class ApplicationAlreadyReviewed(Exception):
    """別のリクエストで申請が処理済みになっている。"""


@dataclass
class ApplicationReviewResult:
    event_detail: EventDetail
    schedule_changes: list[dict[str, str]]
    warnings: list[str]


def review_application(
    event_detail, decision, *, reviewer, request=None, rejection_reason='',
    event=None, start_time=None, duration=None, api_key_id=None,
) -> ApplicationReviewResult:
    """最新行をロックし、pending からの状態変更と後処理を一度だけ行う。

    日時調整の入力検証は画面のフォームが行う。API は日時を変更しない。
    """
    if decision not in ('approved', 'rejected'):
        raise ValueError('承認または却下を指定してください。')

    with transaction.atomic():
        # Vket 同期と同じく、コラボ・参加・イベントを発表詳細より先にロックする。
        lock_application_event(event or event_detail.event)
        locked = get_object_or_404(
            EventDetail.objects.select_for_update(of=('self',)).select_related('event__community'),
            pk=event_detail.pk,
        )
        if not reviewer.is_active or not locked.event.community.is_manager(reviewer):
            raise PermissionDenied('この申請を確認する権限がありません。')
        if locked.status != 'pending':
            raise ApplicationAlreadyReviewed

        schedule_changes = []
        warnings = []
        update_fields = ['status', 'updated_at']
        if decision == 'approved':
            if event is not None:
                schedule_changes = _apply_schedule_changes(locked, event, start_time, duration)
                update_fields.extend(['event', 'start_time', 'duration'])
            from event.services.article_generation import schedule_article_notification_on_approval
            schedule_article_notification_on_approval(locked)
        else:
            locked.rejection_reason = rejection_reason
            update_fields.append('rejection_reason')

        locked.status = decision
        # save を通すことで記事生成・自動投稿などの既存シグナルも実行する。
        locked.save(update_fields=update_fields)
        if decision == 'approved':
            link_approved_application(locked)
            warnings = application_warnings(locked)

    from event.notifications import notify_applicant_of_result
    notify_applicant_of_result(locked, request=request, schedule_changes=schedule_changes)
    logger.info('event_detail_reviewed', extra={
        'event_detail_id': locked.pk,
        'community_id': locked.event.community_id,
        'decision': decision,
        'reviewer_id': reviewer.pk,
        'api_key_id': api_key_id,
    })
    return ApplicationReviewResult(locked, schedule_changes, warnings)


def _apply_schedule_changes(event_detail, event, start_time, duration) -> list[dict[str, str]]:
    """承認時の日時調整を反映し、通知に載せる差分を返す。"""
    if event.community_id != event_detail.event.community_id:
        raise PermissionDenied('他の集会のイベントには変更できません。')

    changes = []
    if event.pk != event_detail.event_id:
        changes.append({
            'label': '開催日',
            'before': event_detail.event.date.strftime('%Y-%m-%d'),
            'after': event.date.strftime('%Y-%m-%d'),
        })
        event_detail.event = event
    if start_time != event_detail.start_time:
        changes.append({
            'label': '開始時刻',
            'before': event_detail.start_time.strftime('%H:%M'),
            'after': start_time.strftime('%H:%M'),
        })
        event_detail.start_time = start_time
    if duration != event_detail.duration:
        changes.append({
            'label': '持ち時間',
            'before': f'{event_detail.duration}分',
            'after': f'{duration}分',
        })
        event_detail.duration = duration
    return changes
