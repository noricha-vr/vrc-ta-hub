"""録画編集確定指示キューの完了・失敗報告および同意撤回時の補償停止処理。"""
from __future__ import annotations

import logging
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from event.models import EventDetail, RecordingEditOrder, RecordingReviewItem
from event.services.recording_ops_common import (
    RecordingConflictError,
    RecordingNotFoundError,
    RecordingValidationError,
    enqueue_compensating_stop_publish,
    is_order_lease_expired,
    is_recording_consent_valid,
    item_has_unstopped_video,
    lock_event_detail,
    mark_order_lease_expired,
    validate_positive_revision,
    validate_safe_youtube_url,
    validate_video_id,
)
from event.youtube_urls import youtube_video_id

logger = logging.getLogger(__name__)

_ALLOWED_COMPLETE_PRIVACIES = frozenset(
    {
        "",
        RecordingReviewItem.YouTubePrivacy.UNLISTED,
        RecordingReviewItem.YouTubePrivacy.PRIVATE,
        RecordingReviewItem.YouTubePrivacy.PUBLIC,
    }
)
_ALLOWED_COMPLETE_STATUSES = frozenset(
    {
        "",
        RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
        RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
        RecordingReviewItem.ReviewStatus.PUBLISHED,
        RecordingReviewItem.ReviewStatus.PRIVATE_FIXED,
    }
)


def _derive_review_status_on_success(
    *,
    order_type: str,
    privacy: str,
    requested_review_status: str,
) -> str:
    if order_type == RecordingEditOrder.OrderType.STOP_PUBLISH:
        if privacy not in ("", RecordingReviewItem.YouTubePrivacy.PRIVATE):
            raise RecordingValidationError("stop_publish の完了報告では youtube_draft_privacy='private' のみ指定できます。")
        if requested_review_status not in ("", RecordingReviewItem.ReviewStatus.PRIVATE_FIXED):
            raise RecordingValidationError("stop_publish の完了報告では review_status='private_fixed' のみ指定できます。")
        return RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
    if (
        requested_review_status == RecordingReviewItem.ReviewStatus.PUBLISHED
        and privacy != RecordingReviewItem.YouTubePrivacy.PUBLIC
    ):
        raise RecordingValidationError("published に遷移するには youtube_draft_privacy='public' が必要です。")
    if (
        privacy == RecordingReviewItem.YouTubePrivacy.PUBLIC
        and requested_review_status
        and requested_review_status != RecordingReviewItem.ReviewStatus.PUBLISHED
    ):
        raise RecordingValidationError("youtube_draft_privacy='public' のときは review_status='published' を指定してください。")
    if (
        requested_review_status == RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
        and privacy != RecordingReviewItem.YouTubePrivacy.PRIVATE
    ):
        raise RecordingValidationError("private_fixed に遷移するには youtube_draft_privacy='private' が必要です。")
    if (
        privacy == RecordingReviewItem.YouTubePrivacy.PRIVATE
        and requested_review_status
        and requested_review_status != RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
    ):
        raise RecordingValidationError("youtube_draft_privacy='private' のときは review_status='private_fixed' を指定してください。")
    if (
        privacy == RecordingReviewItem.YouTubePrivacy.PUBLIC
        or requested_review_status == RecordingReviewItem.ReviewStatus.PUBLISHED
    ):
        return RecordingReviewItem.ReviewStatus.PUBLISHED
    if requested_review_status:
        return requested_review_status
    if privacy == RecordingReviewItem.YouTubePrivacy.PRIVATE:
        return RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
    return RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED


def _apply_succeeded_order(
    *,
    detail: EventDetail,
    item: RecordingReviewItem,
    order: RecordingEditOrder,
    result_video_id: str,
    youtube_url: str,
    youtube_draft_privacy: str,
    review_status: str,
    consent_valid: bool,
) -> bool:
    """完了成功時の状態遷移を適用し、同意撤回・論理削除後の補償 stop_publish が必要かを返す。"""
    item_fields = ["review_status", "updated_at"]
    if order.order_type == RecordingEditOrder.OrderType.STOP_PUBLISH:
        effective_privacy = youtube_draft_privacy or RecordingReviewItem.YouTubePrivacy.PRIVATE
        derived_status = _derive_review_status_on_success(
            order_type=order.order_type,
            privacy=effective_privacy,
            requested_review_status=review_status,
        )
        order.result_video_id = result_video_id
        order.error_message = ""
        item.review_status = derived_status
        item.youtube_draft_privacy = effective_privacy
        item_fields.append("youtube_draft_privacy")
        if detail.youtube_url:
            detail.youtube_url = None
            detail.save(update_fields=["youtube_url", "updated_at"])
        item.save(update_fields=list(dict.fromkeys(item_fields)))
        return False

    effective_privacy = youtube_draft_privacy or item.youtube_draft_privacy
    derived_status = _derive_review_status_on_success(
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
        item.youtube_draft_video_id = result_video_id
        item_fields.append("youtube_draft_video_id")
    if youtube_draft_privacy:
        item.youtube_draft_privacy = youtube_draft_privacy
        item_fields.append("youtube_draft_privacy")
    item.review_status = derived_status
    item.save(update_fields=list(dict.fromkeys(item_fields)))

    is_public_release = (
        derived_status == RecordingReviewItem.ReviewStatus.PUBLISHED
        and effective_privacy == RecordingReviewItem.YouTubePrivacy.PUBLIC
    )
    if consent_valid:
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
        return False

    if detail.youtube_url:
        detail.youtube_url = None
        detail.save(update_fields=["youtube_url", "updated_at"])
    if youtube_url or is_public_release:
        logger.warning(
            "recording_order_youtube_url_skipped",
            extra={
                "event_type": "recording_order_youtube_url_skipped",
                "order_id": order.pk,
                "event_detail_id": detail.pk,
                "reason": "consent_or_deleted",
            },
        )
    return (
        derived_status != RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
        and (
            is_public_release
            or bool(effective_video_id)
            or derived_status == RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED
        )
    )


def _record_partial_or_late_apply_edl_output(
    *,
    detail: EventDetail,
    item: RecordingReviewItem,
    order: RecordingEditOrder,
    reported_video_id: str,
    youtube_draft_privacy: str,
    review_status: str,
    consent_valid: bool,
    now: datetime,
) -> None:
    """失効・部分失敗・遅延再報告の apply_edl から結果動画 ID を保全し、未同意なら停止予約する。"""
    if order.order_type != RecordingEditOrder.OrderType.APPLY_EDL:
        return

    if reported_video_id:
        if not order.result_video_id:
            order.result_video_id = reported_video_id
            order.save(update_fields=["result_video_id", "updated_at"])
        item_fields = ["youtube_draft_video_id", "updated_at"]
        item.youtube_draft_video_id = reported_video_id
        if not consent_valid:
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
        not consent_valid
        and (
            youtube_draft_privacy == RecordingReviewItem.YouTubePrivacy.PUBLIC
            or review_status == RecordingReviewItem.ReviewStatus.PUBLISHED
        )
        and item.youtube_draft_video_id
    ):
        item.youtube_draft_privacy = RecordingReviewItem.YouTubePrivacy.PUBLIC
        item.review_status = RecordingReviewItem.ReviewStatus.PUBLISHED
        item.save(update_fields=["youtube_draft_privacy", "review_status", "updated_at"])

    if consent_valid:
        return

    if detail.youtube_url:
        detail.youtube_url = None
        detail.save(update_fields=["youtube_url", "updated_at"])

    active_orders = list(
        RecordingEditOrder.objects.select_for_update().filter(
            review_item=item,
            status__in=RecordingEditOrder.ACTIVE_STATUSES,
        )
    )
    has_remaining_active = False
    for active in active_orders:
        if (
            active.order_type == RecordingEditOrder.OrderType.APPLY_EDL
            and active.status == RecordingEditOrder.Status.QUEUED
        ):
            active.status = RecordingEditOrder.Status.CANCELED
            active.active_lock_key = None
            active.completed_at = now
            active.error_message = "同意撤回または論理削除により待機中の apply_edl を自動キャンセルしました。"
            active.save(
                update_fields=[
                    "status",
                    "active_lock_key",
                    "completed_at",
                    "error_message",
                    "updated_at",
                ]
            )
        else:
            has_remaining_active = True

    if not has_remaining_active and (bool(reported_video_id) or item_has_unstopped_video(item)):
        enqueue_compensating_stop_publish(
            detail=detail,
            item=item,
            requested_by=order.requested_by,
            source_order_id=order.pk,
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
    if youtube_draft_privacy not in _ALLOWED_COMPLETE_PRIVACIES:
        raise RecordingValidationError(
            "youtube_draft_privacy は '', unlisted, private, public のいずれかで指定してください。"
        )
    if review_status not in _ALLOWED_COMPLETE_STATUSES:
        raise RecordingValidationError("review_status に無効な値が指定されています。")
    if not isinstance(error_message, str):
        raise RecordingValidationError("error_message は文字列で指定してください。")

    ref = RecordingEditOrder.objects.values("review_item_id", "event_detail_id").filter(pk=order_id).first()
    if ref is None:
        raise RecordingNotFoundError("対象の確定指示が見つかりません。")

    conflict_code: str | None = None
    conflict_detail: str = ""
    conflict_state: dict[str, object] = {}

    with transaction.atomic():
        detail = lock_event_detail(ref["event_detail_id"], include_deleted=True)
        item = RecordingReviewItem.objects.select_for_update().get(pk=ref["review_item_id"])
        order = RecordingEditOrder.objects.select_for_update().get(pk=order_id)
        consent_valid = is_recording_consent_valid(detail)
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
                event_detail_id=detail.pk,
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
                    "event_detail_id": detail.pk,
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
            "event_detail_id": detail.pk,
            "order_type": order.order_type,
            "status": order.status,
            "target_revision": order.target_revision,
        },
    )
    return order
