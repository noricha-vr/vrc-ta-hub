"""pending_stop_video_ids と補償 stop_publish 状態機械（Layer 2）。"""
from __future__ import annotations

import copy
import logging
from datetime import datetime
from typing import Any

from event.models import EventDetail, RecordingEditOrder, RecordingReviewItem

from .errors import (
    MAX_REVISION,
    ORDER_LEASE_TIMEOUT,
    RecordingValidationError,
    derive_review_status_on_success,
)

logger = logging.getLogger(__name__)


def clear_detail_youtube_url(detail: EventDetail | None) -> bool:
    """発表の youtube_url が設定されていればクリアして保存する。"""
    if detail is None or not detail.youtube_url:
        return False
    detail.youtube_url = None
    detail.save(update_fields=["youtube_url", "updated_at"])
    return True


def add_pending_stop_video_id(item: RecordingReviewItem, video_id: str) -> bool:
    """停止待ち動画 ID 一覧に未登録の video_id を追加し、変更があれば True を返す。"""
    if not video_id:
        return False
    pending = [str(v) for v in (item.pending_stop_video_ids or []) if isinstance(v, str) and v]
    if video_id in pending:
        return False
    pending.append(video_id)
    item.pending_stop_video_ids = pending
    return True


def remove_pending_stop_video_id(item: RecordingReviewItem, *video_ids: str) -> bool:
    """停止が完了した video_id を停止待ち動画 ID 一覧から除去する。"""
    remove_set = {v for v in video_ids if v}
    if not remove_set:
        return False
    current = [str(v) for v in (item.pending_stop_video_ids or []) if isinstance(v, str) and v]
    updated = [v for v in current if v not in remove_set]
    if len(updated) == len(current):
        return False
    item.pending_stop_video_ids = updated
    return True


def _is_private_video(item: RecordingReviewItem) -> bool:
    """保持中の動画が非公開であり、公開・限定公開状態ではないかを返す。"""
    return (
        item.youtube_draft_privacy == RecordingReviewItem.YouTubePrivacy.PRIVATE
        and item.review_status
        not in (
            RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
            RecordingReviewItem.ReviewStatus.PUBLISHED,
        )
    )


def preserve_unstopped_video_id(
    item: RecordingReviewItem,
    prior_video_id: str,
    new_video_id: str,
) -> bool:
    """置き換え前の動画 ID が非公開でなければ停止待ち一覧へ保全する。"""
    if not prior_video_id or prior_video_id == new_video_id or _is_private_video(item):
        return False
    return add_pending_stop_video_id(item, prior_video_id)


def next_stop_target_video_id(item: RecordingReviewItem) -> str:
    """次に stop_publish が停止対象とすべき動画 ID を返す。"""
    for v in item.pending_stop_video_ids or []:
        if isinstance(v, str) and v:
            return v
    return item.youtube_draft_video_id or ""


def item_has_unstopped_video(item: RecordingReviewItem) -> bool:
    """非公開状態ではなく、停止対象となり得る外部動画または公開状態が存在するか。"""
    if [v for v in (item.pending_stop_video_ids or []) if isinstance(v, str) and v]:
        return True
    if _is_private_video(item):
        return False
    return (
        bool(item.youtube_draft_video_id)
        or item.review_status
        in (RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED, RecordingReviewItem.ReviewStatus.PUBLISHED)
        or item.youtube_draft_privacy
        in (RecordingReviewItem.YouTubePrivacy.UNLISTED, RecordingReviewItem.YouTubePrivacy.PUBLIC)
    )


def enqueue_compensating_stop_publish(
    *,
    detail: EventDetail | None,
    item: RecordingReviewItem,
    target_video_id: str | None = None,
    requested_by: Any = None,
    source_order_id: int | None = None,
) -> RecordingEditOrder:
    """同意撤回・論理削除等に際して補償 stop_publish 指示を自動投入する。"""
    stop_target = target_video_id if target_video_id is not None else next_stop_target_video_id(item)
    item_fields: list[str] = []
    if stop_target and add_pending_stop_video_id(item, stop_target):
        item_fields.append("pending_stop_video_ids")
    if item.compensating_stop_pending:
        item.compensating_stop_pending = False
        item_fields.append("compensating_stop_pending")
    base_revision = item.edl_revision
    if base_revision < MAX_REVISION:
        target_revision = base_revision + 1
        item.edl_revision = target_revision
        item_fields.append("edl_revision")
    else:
        target_revision = base_revision
    if item_fields:
        item_fields.append("updated_at")
        item.save(update_fields=item_fields)
    compensating = RecordingEditOrder.objects.create(
        review_item=item,
        event_detail=detail,
        order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
        target_video_id=stop_target,
        base_revision=base_revision,
        target_revision=target_revision,
        edl_snapshot=copy.deepcopy(item.edl_json or {}),
        status=RecordingEditOrder.Status.QUEUED,
        active_lock_key=f"item:{item.pk}",
        requested_by=requested_by if getattr(requested_by, "is_authenticated", False) else None,
    )
    logger.warning(
        "recording_order_compensating_stop_publish_queued",
        extra={
            "event_type": "recording_order_compensating_stop_publish_queued",
            "order_id": compensating.pk,
            "source_order_id": source_order_id,
            "event_detail_id": detail.pk if detail is not None else None,
        },
    )
    return compensating


def cancel_queued_apply_edl_and_compensate(
    *,
    detail: EventDetail | None,
    item: RecordingReviewItem,
    now: datetime,
    target_detail_for_order: EventDetail | None = None,
    fallback_requested_by: Any = None,
    source_order_id: int | None = None,
    force_stop_if_idle: bool = False,
) -> bool:
    """待機中の apply_edl を取り消し、他に進行中オーダーが無ければ補償 stop_publish を投入する。"""
    active_orders = list(
        RecordingEditOrder.objects.select_for_update().filter(
            review_item=item,
            status__in=RecordingEditOrder.ACTIVE_STATUSES,
        )
    )
    has_remaining_active = False
    requested_by = fallback_requested_by
    for active in active_orders:
        if (
            active.order_type == RecordingEditOrder.OrderType.APPLY_EDL
            and active.status == RecordingEditOrder.Status.QUEUED
        ):
            active.status = RecordingEditOrder.Status.CANCELED
            active.active_lock_key = None
            active.completed_at = now
            active.error_message = "同意撤回または削除により待機中の apply_edl を自動キャンセルしました。"
            active.save(
                update_fields=["status", "active_lock_key", "completed_at", "error_message", "updated_at"]
            )
            if requested_by is None:
                requested_by = active.requested_by
        else:
            has_remaining_active = True

    if not has_remaining_active and (force_stop_if_idle or item_has_unstopped_video(item)):
        enqueue_compensating_stop_publish(
            detail=target_detail_for_order,
            item=item,
            target_video_id=next_stop_target_video_id(item),
            requested_by=requested_by,
            source_order_id=source_order_id,
        )
        return True
    has_running_apply = any(
        a.order_type == RecordingEditOrder.OrderType.APPLY_EDL and a.status == RecordingEditOrder.Status.RUNNING
        for a in active_orders
    )
    if (
        has_remaining_active
        and has_running_apply
        and (force_stop_if_idle or item_has_unstopped_video(item))
        and not item.compensating_stop_pending
    ):
        item.compensating_stop_pending = True
        item.save(update_fields=["compensating_stop_pending", "updated_at"])
    elif not has_remaining_active and not item_has_unstopped_video(item) and item.compensating_stop_pending:
        item.compensating_stop_pending = False
        item.save(update_fields=["compensating_stop_pending", "updated_at"])
    return False


def is_order_lease_expired(order: RecordingEditOrder, *, now: datetime) -> bool:
    """running 状態のオーダーがリース期限を超過しているか判定する。"""
    return order.status == RecordingEditOrder.Status.RUNNING and (
        order.started_at is None or order.started_at <= now - ORDER_LEASE_TIMEOUT
    )


def mark_order_lease_expired(
    order: RecordingEditOrder,
    *,
    now: datetime,
    error_message: str = "ワーカーのリース期限（30分）を超過したため自動失敗終了しました。",
    event_detail_id: int | None = None,
) -> None:
    """リース期限切れの running オーダーを failed に遷移させて排他キーを解放する。"""
    order.status = RecordingEditOrder.Status.FAILED
    order.active_lock_key = None
    order.completed_at = now
    order.error_message = error_message
    order.save(update_fields=["status", "active_lock_key", "completed_at", "error_message", "updated_at"])
    logger.warning(
        "recording_order_lease_expired",
        extra={
            "event_type": "recording_order_lease_expired",
            "order_id": order.pk,
            "event_detail_id": event_detail_id if event_detail_id is not None else order.event_detail_id,
            "order_type": order.order_type,
        },
    )


def apply_stop_publish_completion(
    *,
    detail: EventDetail | None,
    item: RecordingReviewItem,
    order: RecordingEditOrder,
    result_video_id: str,
    youtube_draft_privacy: str,
    review_status: str,
) -> bool:
    """stop_publish 完了を適用し、未停止動画が残っていれば追加 stop_publish の要否を返す。"""
    item_fields = ["review_status", "updated_at"]
    effective_privacy = youtube_draft_privacy or RecordingReviewItem.YouTubePrivacy.PRIVATE
    derive_review_status_on_success(
        order_type=order.order_type, privacy=effective_privacy, requested_review_status=review_status
    )
    if order.target_video_id and result_video_id and result_video_id != order.target_video_id:
        raise RecordingValidationError("stop_publish の完了報告の動画 ID が指示対象（target_video_id）と一致しません。")
    stopped_vid = result_video_id or order.target_video_id
    order.result_video_id = stopped_vid
    order.error_message = ""
    clear_detail_youtube_url(detail)

    if remove_pending_stop_video_id(item, stopped_vid):
        item_fields.append("pending_stop_video_ids")

    if (
        item.compensating_stop_pending
        and item.review_status == RecordingReviewItem.ReviewStatus.PUBLISHED
        and item.youtube_draft_privacy == RecordingReviewItem.YouTubePrivacy.PUBLIC
    ):
        # 実行中の stop_publish の間に新たな公開報告が割り込んだため、別動画残存の有無に関わらず再停止予約を保全する。
        item.compensating_stop_pending = False
        item_fields.append("compensating_stop_pending")
        if stopped_vid and add_pending_stop_video_id(item, stopped_vid):
            item_fields.append("pending_stop_video_ids")
        item.review_status = RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED
        item.youtube_draft_privacy = ""
        item_fields.append("youtube_draft_privacy")

    remaining_pending = [v for v in (item.pending_stop_video_ids or []) if isinstance(v, str) and v]
    untracked_current = bool(item.youtube_draft_video_id) and item.youtube_draft_video_id != stopped_vid
    if remaining_pending or untracked_current:
        if untracked_current and add_pending_stop_video_id(item, item.youtube_draft_video_id):
            item_fields.append("pending_stop_video_ids")
        elif item.youtube_draft_video_id == stopped_vid and remaining_pending:
            item.youtube_draft_video_id = remaining_pending[0]
            item_fields.append("youtube_draft_video_id")
        if item.review_status == RecordingReviewItem.ReviewStatus.PRIVATE_FIXED:
            item.review_status = RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED
        if item.youtube_draft_privacy == RecordingReviewItem.YouTubePrivacy.PRIVATE:
            item.youtube_draft_privacy = ""
            item_fields.append("youtube_draft_privacy")
        item.save(update_fields=list(dict.fromkeys(item_fields)))
        return True

    item.review_status = RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
    item.youtube_draft_privacy = effective_privacy
    item_fields.append("youtube_draft_privacy")
    if item.compensating_stop_pending:
        item.compensating_stop_pending = False
        item_fields.append("compensating_stop_pending")
    item.save(update_fields=list(dict.fromkeys(item_fields)))
    return False


def _sync_late_reported_video_state(
    *,
    item: RecordingReviewItem,
    order: RecordingEditOrder,
    reported_video_id: str,
    youtube_draft_privacy: str,
    review_status: str,
    needs_stop_for_reported: bool,
) -> None:
    privacy_choices = RecordingReviewItem.YouTubePrivacy
    status_choices = RecordingReviewItem.ReviewStatus
    is_public_report = youtube_draft_privacy == privacy_choices.PUBLIC or review_status == status_choices.PUBLISHED
    if reported_video_id:
        if not order.result_video_id:
            order.result_video_id = reported_video_id
            order.save(update_fields=["result_video_id", "updated_at"])
        item_fields = ["youtube_draft_video_id", "updated_at"]
        prior_vid = item.youtube_draft_video_id
        # 同意状態や停止指示有無にかかわらず、別動画で上書きされる旧未停止 ID と遅延 ID を両方保全する
        if preserve_unstopped_video_id(item, prior_vid, reported_video_id):
            item_fields.append("pending_stop_video_ids")
        if (
            (needs_stop_for_reported or (prior_vid and prior_vid != reported_video_id))
            and youtube_draft_privacy != privacy_choices.PRIVATE
            and add_pending_stop_video_id(item, reported_video_id)
        ):
            item_fields.append("pending_stop_video_ids")

        item.youtube_draft_video_id = reported_video_id
        if is_public_report:
            item.youtube_draft_privacy = privacy_choices.PUBLIC
            item.review_status = status_choices.PUBLISHED
            item_fields.extend(["youtube_draft_privacy", "review_status"])
        elif youtube_draft_privacy == privacy_choices.UNLISTED:
            item.youtube_draft_privacy = privacy_choices.UNLISTED
            item_fields.append("youtube_draft_privacy")
            if item.review_status == status_choices.PRIVATE_FIXED:
                item.review_status = status_choices.UNLISTED_SCHEDULED
                item_fields.append("review_status")
        elif item.review_status == status_choices.PRIVATE_FIXED or (
            prior_vid and prior_vid != reported_video_id and not youtube_draft_privacy
        ):
            if item.review_status == status_choices.PRIVATE_FIXED:
                item.review_status = status_choices.NEEDS_REVIEW
                item_fields.append("review_status")
            if not youtube_draft_privacy and item.youtube_draft_privacy:
                item.youtube_draft_privacy = ""
                item_fields.append("youtube_draft_privacy")
        item.save(update_fields=list(dict.fromkeys(item_fields)))
    elif is_public_report:
        item_fields = ["youtube_draft_privacy", "review_status", "updated_at"]
        if needs_stop_for_reported and item.youtube_draft_video_id and add_pending_stop_video_id(item, item.youtube_draft_video_id):
            item_fields.append("pending_stop_video_ids")
        item.youtube_draft_privacy = privacy_choices.PUBLIC
        item.review_status = status_choices.PUBLISHED
        item.save(update_fields=item_fields)


def record_partial_or_late_apply_edl_output(
    *,
    detail: EventDetail | None,
    item: RecordingReviewItem,
    order: RecordingEditOrder,
    reported_video_id: str,
    youtube_draft_privacy: str,
    review_status: str,
    consent_valid: bool,
    now: datetime,
) -> None:
    """失効・部分失敗・遅延再報告の apply_edl から結果動画 ID・公開履歴を保全し、必要なら停止予約する。"""
    if order.order_type != RecordingEditOrder.OrderType.APPLY_EDL:
        return

    active_orders = list(
        RecordingEditOrder.objects.select_for_update().filter(
            review_item=item, status__in=RecordingEditOrder.ACTIVE_STATUSES
        )
    )
    has_active_stop = any(a.order_type == RecordingEditOrder.OrderType.STOP_PUBLISH for a in active_orders)
    has_newer_stop = RecordingEditOrder.objects.filter(
        review_item=item,
        order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
        status__in=(RecordingEditOrder.Status.QUEUED, RecordingEditOrder.Status.RUNNING, RecordingEditOrder.Status.SUCCEEDED),
        pk__gt=order.pk,
    ).exists()

    _sync_late_reported_video_state(
        item=item,
        order=order,
        reported_video_id=reported_video_id,
        youtube_draft_privacy=youtube_draft_privacy,
        review_status=review_status,
        needs_stop_for_reported=(not consent_valid or has_active_stop or has_newer_stop),
    )

    if consent_valid and not has_newer_stop and not item.compensating_stop_pending:
        return

    clear_detail_youtube_url(detail)
    cancel_queued_apply_edl_and_compensate(
        detail=detail,
        item=item,
        now=now,
        target_detail_for_order=detail,
        fallback_requested_by=order.requested_by,
        source_order_id=order.pk,
        force_stop_if_idle=bool(reported_video_id),
    )
