"""同意撤回・論理削除・物理削除時の録画公開停止予約窓口（Layer 3）。"""
from __future__ import annotations

from datetime import datetime

from django.db import transaction
from django.utils import timezone

from community.models import Community
from event.models import Event, EventDetail, RecordingReviewItem

from .locks import is_recording_consent_valid
from .stop_queue import cancel_queued_apply_edl_and_compensate, clear_detail_youtube_url


def _compensate_revoked_item(
    *,
    detail: EventDetail | None,
    item: RecordingReviewItem,
    now: datetime,
    target_detail_for_order: EventDetail | None = None,
) -> bool:
    """同意撤回・論理削除・物理削除された項目について公開 URL を消し、未完了 apply_edl 取消と停止予約を行う。"""
    clear_detail_youtube_url(detail)
    return cancel_queued_apply_edl_and_compensate(
        detail=detail,
        item=item,
        now=now,
        target_detail_for_order=target_detail_for_order,
    )


def _compensate_revoked_detail_ids(detail_ids: list[int], now: datetime) -> int:
    queued_count = 0
    for detail_id in detail_ids:
        detail = (
            EventDetail.all_objects.select_for_update(of=("self",))
            .select_related("event__community")
            .filter(pk=detail_id)
            .first()
        )
        if detail is None or is_recording_consent_valid(detail):
            continue
        item = RecordingReviewItem.objects.select_for_update().filter(event_detail=detail).first()
        if item is not None and _compensate_revoked_item(
            detail=detail,
            item=item,
            now=now,
            target_detail_for_order=detail,
        ):
            queued_count += 1
    return queued_count


def revoke_recording_for_detail(detail_id: int) -> bool:
    """発表単位の同意撤回・論理削除に合わせて、録画停止を予約する（EventDetail -> Item -> Order 順でロック）。"""
    with transaction.atomic():
        detail = (
            EventDetail.all_objects.select_for_update(of=("self",))
            .select_related("event__community")
            .filter(pk=detail_id)
            .first()
        )
        if detail is None or is_recording_consent_valid(detail):
            return False
        item = RecordingReviewItem.objects.select_for_update().filter(event_detail=detail).first()
        if item is None:
            return False
        return _compensate_revoked_item(
            detail=detail,
            item=item,
            now=timezone.now(),
            target_detail_for_order=detail,
        )


def revoke_recording_on_detail_delete(detail_id: int) -> bool:
    """発表または親イベントの物理削除直前に、撮影成果物の停止予約を切り離して保全する。"""
    with transaction.atomic():
        item = RecordingReviewItem.objects.select_for_update().filter(event_detail_id=detail_id).first()
        if item is None:
            return False
        return _compensate_revoked_item(
            detail=None,
            item=item,
            now=timezone.now(),
            target_detail_for_order=None,
        )


def revoke_recording_for_community(community_id: int) -> int:
    """集会の録画許可撤回に合わせて、配下発表の録画停止を一括予約する（Community -> EventDetail -> Item -> Order 順でロック）。"""
    with transaction.atomic():
        community = Community.objects.select_for_update().filter(pk=community_id).first()
        if community is None or community.recording_allowed:
            return 0
        detail_ids = list(
            RecordingReviewItem.objects.filter(event_detail__event__community_id=community_id)
            .order_by("event_detail_id")
            .values_list("event_detail_id", flat=True)
        )
        return _compensate_revoked_detail_ids(detail_ids, timezone.now())


def revoke_recording_for_event(event_id: int) -> int:
    """イベントの所属集会変更等で録画不許可となった配下発表の停止予約を行う（Community -> EventDetail -> Item -> Order 順）。"""
    community_id = Event.objects.filter(pk=event_id).values_list("community_id", flat=True).first()
    if community_id is None:
        return 0
    with transaction.atomic():
        Community.objects.select_for_update().filter(pk=community_id).first()
        detail_ids = list(
            RecordingReviewItem.objects.filter(event_detail__event_id=event_id)
            .order_by("event_detail_id")
            .values_list("event_detail_id", flat=True)
        )
        return _compensate_revoked_detail_ids(detail_ids, timezone.now())
