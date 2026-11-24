"""録画編集確定指示キューの完了・失敗報告処理（Layer 3）。"""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from event.models import EventDetail, RecordingEditOrder, RecordingReviewItem
from event.youtube_urls import youtube_video_id

from .errors import (
    ALLOWED_COMPLETE_PRIVACIES,
    ALLOWED_COMPLETE_STATUSES,
    RecordingConflictError,
    RecordingNotFoundError,
    RecordingValidationError,
    derive_review_status_on_success,
    validate_positive_revision,
    validate_safe_youtube_url,
    validate_video_id,
)
from .locks import is_recording_consent_valid, lock_event_detail_or_none
from .stop_queue import (
    add_pending_stop_video_id,
    apply_stop_publish_completion,
    clear_detail_youtube_url,
    enqueue_compensating_stop_publish,
    is_order_lease_expired,
    item_has_unstopped_video,
    mark_order_lease_expired,
    next_stop_target_video_id,
    preserve_unstopped_video_id,
    record_partial_or_late_apply_edl_output,
)

logger = logging.getLogger(__name__)


def _validate_completion_inputs(
    *,
    target_revision: int,
    status: str,
    result_video_id: str,
    youtube_url: str,
    youtube_draft_privacy: str,
    review_status: str,
    error_message: str,
) -> str:
    validate_positive_revision(target_revision, "target_revision")
    if status not in (
        RecordingEditOrder.Status.SUCCEEDED,
        RecordingEditOrder.Status.FAILED,
    ):
        raise RecordingValidationError("status は succeeded または failed を指定してください。")

    validate_video_id(result_video_id, "result_video_id")
    normalized_video_id = result_video_id
    if youtube_url:
        validate_safe_youtube_url(youtube_url)
        parsed_vid = youtube_video_id(youtube_url) or ""
        if normalized_video_id and normalized_video_id != parsed_vid:
            raise RecordingValidationError("result_video_id と youtube_url の動画 ID が一致しません。")
        if not normalized_video_id:
            normalized_video_id = parsed_vid
    if youtube_draft_privacy not in ALLOWED_COMPLETE_PRIVACIES:
        raise RecordingValidationError(
            "youtube_draft_privacy は '', unlisted, private, public のいずれかで指定してください。"
        )
    if review_status not in ALLOWED_COMPLETE_STATUSES:
        raise RecordingValidationError("review_status に無効な値が指定されています。")
    if not isinstance(error_message, str):
        raise RecordingValidationError("error_message は文字列で指定してください。")
    return normalized_video_id


def _apply_succeeded_order(
    *,
    detail: EventDetail | None,
    item: RecordingReviewItem,
    order: RecordingEditOrder,
    result_video_id: str,
    youtube_url: str,
    youtube_draft_privacy: str,
    review_status: str,
    consent_valid: bool,
) -> bool:
    """完了成功時の状態遷移を適用し、追加または補償 stop_publish が必要かを返す。"""
    if order.order_type == RecordingEditOrder.OrderType.STOP_PUBLISH:
        return apply_stop_publish_completion(
            detail=detail,
            item=item,
            order=order,
            result_video_id=result_video_id,
            youtube_draft_privacy=youtube_draft_privacy,
            review_status=review_status,
        )

    item_fields = ["review_status", "updated_at"]
    is_new_video = bool(result_video_id) and result_video_id != item.youtube_draft_video_id
    effective_privacy = (
        youtube_draft_privacy
        if (youtube_draft_privacy or is_new_video)
        else item.youtube_draft_privacy
    )
    derived_status = derive_review_status_on_success(
        order_type=order.order_type,
        privacy=effective_privacy,
        requested_review_status=review_status,
    )
    effective_video_id = result_video_id or item.youtube_draft_video_id
    if derived_status == RecordingReviewItem.ReviewStatus.PUBLISHED and not effective_video_id:
        raise RecordingValidationError(
            "公開済み（published）として完了するには動画 ID（result_video_id または youtube_url）が必要です。"
        )

    order.result_video_id = result_video_id
    order.error_message = ""
    if result_video_id:
        prior_vid = item.youtube_draft_video_id
        if preserve_unstopped_video_id(item, prior_vid, result_video_id):
            item_fields.append("pending_stop_video_ids")
        item.youtube_draft_video_id = result_video_id
        item_fields.append("youtube_draft_video_id")
    if youtube_draft_privacy or is_new_video:
        item.youtube_draft_privacy = youtube_draft_privacy
        item_fields.append("youtube_draft_privacy")
    item.review_status = derived_status
    if not consent_valid and derived_status != RecordingReviewItem.ReviewStatus.PRIVATE_FIXED:
        if effective_video_id and add_pending_stop_video_id(item, effective_video_id):
            item_fields.append("pending_stop_video_ids")
    pending_videos = [v for v in (item.pending_stop_video_ids or []) if isinstance(v, str) and v]
    if pending_videos and derived_status == RecordingReviewItem.ReviewStatus.PRIVATE_FIXED:
        # Unstopped prior videos remain, so the item must not transition to private_fixed
        # or lose track of the video that still requires emergency stop.
        item.review_status = RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED
        item.youtube_draft_privacy = ""
        item.youtube_draft_video_id = pending_videos[0]
        item_fields.extend(["youtube_draft_privacy", "youtube_draft_video_id"])
    item.save(update_fields=list(dict.fromkeys(item_fields)))

    is_public_release = (
        derived_status == RecordingReviewItem.ReviewStatus.PUBLISHED
        and effective_privacy == RecordingReviewItem.YouTubePrivacy.PUBLIC
    )
    if consent_valid and detail is not None:
        if youtube_url:
            if is_public_release and youtube_url != (detail.youtube_url or ""):
                detail.youtube_url = youtube_url
                detail.save(update_fields=["youtube_url", "updated_at"])
            elif not is_public_release:
                logger.warning(
                    "recording_order_youtube_url_skipped",
                    extra={
                        "event_type": "recording_order_youtube_url_skipped",
                        "order_id": order.pk,
                        "event_detail_id": detail.pk,
                        "reason": "not_published",
                    },
                )
        needs_deferred_stop = item.compensating_stop_pending and item_has_unstopped_video(item)
        if item.compensating_stop_pending and not needs_deferred_stop:
            item.compensating_stop_pending = False
            item.save(update_fields=["compensating_stop_pending", "updated_at"])
        return needs_deferred_stop or bool(
            pending_videos and derived_status == RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
        )

    clear_detail_youtube_url(detail)
    if youtube_url or is_public_release:
        logger.warning(
            "recording_order_youtube_url_skipped",
            extra={
                "event_type": "recording_order_youtube_url_skipped",
                "order_id": order.pk,
                "event_detail_id": detail.pk if detail is not None else None,
                "reason": "consent_or_deleted",
            },
        )
    has_pending_stop = bool(pending_videos)
    needs_stop = (
        has_pending_stop
        or (
            derived_status != RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
            and (
                is_public_release
                or bool(effective_video_id)
                or derived_status == RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED
            )
        )
        or (item.compensating_stop_pending and item_has_unstopped_video(item))
    )
    if not needs_stop and item.compensating_stop_pending:
        item.compensating_stop_pending = False
        item.save(update_fields=["compensating_stop_pending", "updated_at"])
    return needs_stop


def _order_conflict_state(order: RecordingEditOrder) -> dict[str, object]:
    return {
        "order_id": order.pk,
        "status": order.status,
        "target_revision": order.target_revision,
    }


def complete_edit_order(
    order_id: int,
    *,
    target_revision: int,
    status: str,
    result_video_id: str = "",
    youtube_url: str = "",
    youtube_draft_privacy: str = "",
    review_status: str = "",
    error_message: str = "",
) -> RecordingEditOrder:
    """running 状態のオーダーの完了/失敗を確定し、ロックを解放する。"""
    result_video_id = _validate_completion_inputs(
        target_revision=target_revision,
        status=status,
        result_video_id=result_video_id,
        youtube_url=youtube_url,
        youtube_draft_privacy=youtube_draft_privacy,
        review_status=review_status,
        error_message=error_message,
    )

    ref = RecordingEditOrder.objects.values("review_item_id", "event_detail_id").filter(pk=order_id).first()
    if ref is None:
        raise RecordingNotFoundError("対象の確定指示が見つかりません。")

    conflict_code: str | None = None
    conflict_detail = ""
    conflict_state: dict[str, object] = {}
    detail_id_for_log: int | None = None

    with transaction.atomic():
        detail = (
            lock_event_detail_or_none(ref["event_detail_id"], include_deleted=True)
            if ref["event_detail_id"] is not None
            else None
        )
        item = RecordingReviewItem.objects.select_for_update().get(pk=ref["review_item_id"])
        if item.event_detail_id is None:
            detail = None
        detail_id_for_log = detail.pk if detail is not None else None
        order = RecordingEditOrder.objects.select_for_update().get(pk=order_id)
        consent_valid = detail is not None and is_recording_consent_valid(detail)
        now = timezone.now()

        if order.status != RecordingEditOrder.Status.RUNNING:
            if (
                order.order_type == RecordingEditOrder.OrderType.APPLY_EDL
                and order.status == RecordingEditOrder.Status.FAILED
                and order.target_revision == target_revision
                and (
                    bool(result_video_id)
                    or youtube_draft_privacy == RecordingReviewItem.YouTubePrivacy.PUBLIC
                    or review_status == RecordingReviewItem.ReviewStatus.PUBLISHED
                    or not consent_valid
                )
            ):
                record_partial_or_late_apply_edl_output(
                    detail=detail,
                    item=item,
                    order=order,
                    reported_video_id=result_video_id,
                    youtube_draft_privacy=youtube_draft_privacy,
                    review_status=review_status,
                    consent_valid=consent_valid,
                    now=now,
                )
            conflict_code = "order_not_running"
            conflict_detail = "処理中（running）ではないオーダーは完了報告できません。"
            conflict_state = _order_conflict_state(order)
        elif order.target_revision != target_revision:
            conflict_code = "target_revision_mismatch"
            conflict_detail = "target_revision が現在のオーダーと一致しません。"
            conflict_state = _order_conflict_state(order)
        elif is_order_lease_expired(order, now=now):
            mark_order_lease_expired(
                order,
                now=now,
                error_message="ワーカーのリース期限（30分）を超過したため完了報告を拒否しました。",
                event_detail_id=detail_id_for_log,
            )
            record_partial_or_late_apply_edl_output(
                detail=detail,
                item=item,
                order=order,
                reported_video_id=result_video_id,
                youtube_draft_privacy=youtube_draft_privacy,
                review_status=review_status,
                consent_valid=consent_valid,
                now=now,
            )
            conflict_code = "order_lease_expired"
            conflict_detail = "リース期限（30分）を超過したオーダーは完了報告できません。"
            conflict_state = _order_conflict_state(order)
        else:
            compensating_stop_needed = False
            if status == RecordingEditOrder.Status.SUCCEEDED:
                compensating_stop_needed = _apply_succeeded_order(
                    detail=detail,
                    item=item,
                    order=order,
                    result_video_id=result_video_id,
                    youtube_url=youtube_url,
                    youtube_draft_privacy=youtube_draft_privacy,
                    review_status=review_status,
                    consent_valid=consent_valid,
                )
            else:
                order.error_message = error_message[:10000]

            order.status = status
            order.active_lock_key = None
            order.completed_at = now
            order.save(
                update_fields=[
                    "status",
                    "active_lock_key",
                    "completed_at",
                    "result_video_id",
                    "error_message",
                    "updated_at",
                ]
            )
            if status == RecordingEditOrder.Status.FAILED:
                record_partial_or_late_apply_edl_output(
                    detail=detail,
                    item=item,
                    order=order,
                    reported_video_id=result_video_id,
                    youtube_draft_privacy=youtube_draft_privacy,
                    review_status=review_status,
                    consent_valid=consent_valid,
                    now=now,
                )
            elif compensating_stop_needed:
                enqueue_compensating_stop_publish(
                    detail=detail,
                    item=item,
                    target_video_id=next_stop_target_video_id(item),
                    requested_by=order.requested_by,
                    source_order_id=order.pk,
                )

    if conflict_code is not None:
        if conflict_code != "order_lease_expired":
            logger.warning(
                "recording_order_complete_conflict",
                extra={
                    "event_type": "recording_order_complete_conflict",
                    "order_id": order.pk,
                    "event_detail_id": detail_id_for_log,
                    "reason": conflict_code,
                },
            )
        raise RecordingConflictError(
            conflict_detail,
            code=conflict_code,
            current_state=conflict_state,
        )

    logger.info(
        "recording_order_completed",
        extra={
            "event_type": "recording_order_completed",
            "order_id": order.pk,
            "event_detail_id": detail_id_for_log,
            "order_type": order.order_type,
            "status": order.status,
            "target_revision": order.target_revision,
        },
    )
    return order
