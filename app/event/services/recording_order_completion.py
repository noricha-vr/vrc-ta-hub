"""録画編集確定指示キューの完了・失敗報告および同意撤回時の補償停止処理。"""
from __future__ import annotations

import logging
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from event.models import EventDetail, RecordingEditOrder, RecordingReviewItem
from event.services.recording_ops_common import (
    ALLOWED_COMPLETE_PRIVACIES,
    ALLOWED_COMPLETE_STATUSES,
    RecordingConflictError,
    RecordingNotFoundError,
    RecordingValidationError,
    add_pending_stop_video_id,
    cancel_queued_apply_edl_and_compensate,
    derive_review_status_on_success,
    enqueue_compensating_stop_publish,
    is_order_lease_expired,
    is_recording_consent_valid,
    lock_event_detail_or_none,
    mark_order_lease_expired,
    next_stop_target_video_id,
    remove_pending_stop_video_id,
    validate_positive_revision,
    validate_safe_youtube_url,
    validate_video_id,
)
from event.youtube_urls import youtube_video_id

logger = logging.getLogger(__name__)


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
    item_fields = ["review_status", "updated_at"]
    if order.order_type == RecordingEditOrder.OrderType.STOP_PUBLISH:
        effective_privacy = youtube_draft_privacy or RecordingReviewItem.YouTubePrivacy.PRIVATE
        derive_review_status_on_success(
            order_type=order.order_type,
            privacy=effective_privacy,
            requested_review_status=review_status,
        )
        if order.target_video_id and result_video_id and result_video_id != order.target_video_id:
            raise RecordingValidationError(
                "stop_publish の完了報告の動画 ID が指示対象（target_video_id）と一致しません。"
            )
        stopped_vid = result_video_id or order.target_video_id
        order.result_video_id = stopped_vid
        order.error_message = ""
        if detail is not None and detail.youtube_url:
            detail.youtube_url = None
            detail.save(update_fields=["youtube_url", "updated_at"])

        if remove_pending_stop_video_id(item, stopped_vid):
            item_fields.append("pending_stop_video_ids")

        remaining_pending = [v for v in (item.pending_stop_video_ids or []) if isinstance(v, str) and v]
        untracked_current = bool(item.youtube_draft_video_id) and item.youtube_draft_video_id != stopped_vid
        if remaining_pending or untracked_current:
            if untracked_current and add_pending_stop_video_id(item, item.youtube_draft_video_id):
                item_fields.append("pending_stop_video_ids")
                remaining_pending = [v for v in (item.pending_stop_video_ids or []) if isinstance(v, str) and v]
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
        item.save(update_fields=list(dict.fromkeys(item_fields)))
        return False

    effective_privacy = youtube_draft_privacy or item.youtube_draft_privacy
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
        if (
            prior_vid
            and prior_vid != result_video_id
            and (
                item.review_status != RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
                or item.youtube_draft_privacy != RecordingReviewItem.YouTubePrivacy.PRIVATE
            )
            and add_pending_stop_video_id(item, prior_vid)
        ):
            item_fields.append("pending_stop_video_ids")
        item.youtube_draft_video_id = result_video_id
        item_fields.append("youtube_draft_video_id")
    if youtube_draft_privacy:
        item.youtube_draft_privacy = youtube_draft_privacy
        item_fields.append("youtube_draft_privacy")
    item.review_status = derived_status
    if not consent_valid and derived_status != RecordingReviewItem.ReviewStatus.PRIVATE_FIXED:
        vid_to_stop = effective_video_id
        if vid_to_stop and add_pending_stop_video_id(item, vid_to_stop):
            item_fields.append("pending_stop_video_ids")
    pending_videos = [
        v for v in (item.pending_stop_video_ids or []) if isinstance(v, str) and v
    ]
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
        return bool(
            pending_videos
            and derived_status == RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
        )

    if detail is not None and detail.youtube_url:
        detail.youtube_url = None
        detail.save(update_fields=["youtube_url", "updated_at"])
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
    return has_pending_stop or (
        derived_status != RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
        and (
            is_public_release
            or bool(effective_video_id)
            or derived_status == RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED
        )
    )


def _record_partial_or_late_apply_edl_output(
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
            review_item=item,
            status__in=RecordingEditOrder.ACTIVE_STATUSES,
        )
    )
    has_active_stop = any(
        a.order_type == RecordingEditOrder.OrderType.STOP_PUBLISH for a in active_orders
    )

    if reported_video_id:
        if not order.result_video_id:
            order.result_video_id = reported_video_id
            order.save(update_fields=["result_video_id", "updated_at"])
        item_fields = ["youtube_draft_video_id", "updated_at"]
        prior_vid = item.youtube_draft_video_id
        # 同意状態や停止指示有無にかかわらず、別動画で上書きされる旧未停止 ID と遅延 ID を両方保全する
        if prior_vid and prior_vid != reported_video_id:
            if (
                item.review_status != RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
                or item.youtube_draft_privacy != RecordingReviewItem.YouTubePrivacy.PRIVATE
            ) and add_pending_stop_video_id(item, prior_vid):
                item_fields.append("pending_stop_video_ids")
        if (
            (not consent_valid or has_active_stop or (prior_vid and prior_vid != reported_video_id))
            and youtube_draft_privacy != RecordingReviewItem.YouTubePrivacy.PRIVATE
            and add_pending_stop_video_id(item, reported_video_id)
        ):
            item_fields.append("pending_stop_video_ids")

        item.youtube_draft_video_id = reported_video_id
        if (
            youtube_draft_privacy == RecordingReviewItem.YouTubePrivacy.PUBLIC
            or review_status == RecordingReviewItem.ReviewStatus.PUBLISHED
        ):
            item.youtube_draft_privacy = RecordingReviewItem.YouTubePrivacy.PUBLIC
            item.review_status = RecordingReviewItem.ReviewStatus.PUBLISHED
            item_fields.extend(["youtube_draft_privacy", "review_status"])
        elif youtube_draft_privacy == RecordingReviewItem.YouTubePrivacy.UNLISTED:
            item.youtube_draft_privacy = RecordingReviewItem.YouTubePrivacy.UNLISTED
            item_fields.append("youtube_draft_privacy")
            if item.review_status == RecordingReviewItem.ReviewStatus.PRIVATE_FIXED:
                item.review_status = RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED
                item_fields.append("review_status")
        elif item.review_status == RecordingReviewItem.ReviewStatus.PRIVATE_FIXED:
            item.review_status = RecordingReviewItem.ReviewStatus.NEEDS_REVIEW
            item_fields.append("review_status")
        item.save(update_fields=list(dict.fromkeys(item_fields)))
    elif (
        youtube_draft_privacy == RecordingReviewItem.YouTubePrivacy.PUBLIC
        or review_status == RecordingReviewItem.ReviewStatus.PUBLISHED
    ):
        item_fields = ["youtube_draft_privacy", "review_status", "updated_at"]
        if (
            (not consent_valid or has_active_stop)
            and item.youtube_draft_video_id
            and add_pending_stop_video_id(item, item.youtube_draft_video_id)
        ):
            item_fields.append("pending_stop_video_ids")
        item.youtube_draft_privacy = RecordingReviewItem.YouTubePrivacy.PUBLIC
        item.review_status = RecordingReviewItem.ReviewStatus.PUBLISHED
        item.save(update_fields=item_fields)

    if consent_valid:
        return

    if detail is not None and detail.youtube_url:
        detail.youtube_url = None
        detail.save(update_fields=["youtube_url", "updated_at"])

    cancel_queued_apply_edl_and_compensate(
        detail=detail,
        item=item,
        now=now,
        target_detail_for_order=detail,
        fallback_requested_by=order.requested_by,
        source_order_id=order.pk,
        force_stop_if_idle=bool(reported_video_id),
    )


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
    validate_positive_revision(target_revision, "target_revision")
    if status not in (
        RecordingEditOrder.Status.SUCCEEDED,
        RecordingEditOrder.Status.FAILED,
    ):
        raise RecordingValidationError("status は succeeded または failed を指定してください。")

    validate_video_id(result_video_id, "result_video_id")
    if youtube_url:
        validate_safe_youtube_url(youtube_url)
        parsed_vid = youtube_video_id(youtube_url) or ""
        if result_video_id and result_video_id != parsed_vid:
            raise RecordingValidationError("result_video_id と youtube_url の動画 ID が一致しません。")
        if not result_video_id:
            result_video_id = parsed_vid
    if youtube_draft_privacy not in ALLOWED_COMPLETE_PRIVACIES:
        raise RecordingValidationError(
            "youtube_draft_privacy は '', unlisted, private, public のいずれかで指定してください。"
        )
    if review_status not in ALLOWED_COMPLETE_STATUSES:
        raise RecordingValidationError("review_status に無効な値が指定されています。")
    if not isinstance(error_message, str):
        raise RecordingValidationError("error_message は文字列で指定してください。")

    ref = RecordingEditOrder.objects.values("review_item_id", "event_detail_id").filter(pk=order_id).first()
    if ref is None:
        raise RecordingNotFoundError("対象の確定指示が見つかりません。")

    conflict_code: str | None = None
    conflict_detail: str = ""
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
                _record_partial_or_late_apply_edl_output(
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
            conflict_state = {
                "order_id": order.pk,
                "status": order.status,
                "target_revision": order.target_revision,
            }
        elif order.target_revision != target_revision:
            conflict_code = "target_revision_mismatch"
            conflict_detail = "target_revision が現在のオーダーと一致しません。"
            conflict_state = {
                "order_id": order.pk,
                "status": order.status,
                "target_revision": order.target_revision,
            }
        elif is_order_lease_expired(order, now=now):
            mark_order_lease_expired(
                order,
                now=now,
                error_message="ワーカーのリース期限（30分）を超過したため完了報告を拒否しました。",
                event_detail_id=detail_id_for_log,
            )
            _record_partial_or_late_apply_edl_output(
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
            conflict_state = {
                "order_id": order.pk,
                "status": order.status,
                "target_revision": order.target_revision,
            }
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
                _record_partial_or_late_apply_edl_output(
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
