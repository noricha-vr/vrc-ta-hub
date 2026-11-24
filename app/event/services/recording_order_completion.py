"""録画編集確定指示キューの完了・失敗報告および同意撤回時の補償停止処理。"""
from __future__ import annotations

import copy
import logging

from django.db import transaction
from django.utils import timezone

from community.constants import RecordingPolicy
from event.models import EventDetail, RecordingEditOrder, RecordingReviewItem
from event.services.recording_ops_common import (
    MAX_REVISION,
    RecordingConflictError,
    RecordingNotFoundError,
    RecordingValidationError,
    is_order_lease_expired,
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

    consent_valid = (
        detail.deleted_at is None
        and detail.recording_policy == RecordingPolicy.PUBLIC
        and bool(detail.event.community.recording_allowed)
    )
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


def _enqueue_compensating_stop_publish(
    *,
    detail: EventDetail,
    item: RecordingReviewItem,
    completed_order: RecordingEditOrder,
) -> RecordingEditOrder:
    """同意撤回・論理削除後に完了した apply_edl に対する補償 stop_publish を自動投入する。"""
    base_revision = item.edl_revision
    if base_revision < MAX_REVISION:
        target_revision = base_revision + 1
        item.edl_revision = target_revision
        item.save(update_fields=["edl_revision", "updated_at"])
    else:
        target_revision = base_revision
    compensating = RecordingEditOrder.objects.create(
        review_item=item,
        event_detail=detail,
        order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
        base_revision=base_revision,
        target_revision=target_revision,
        edl_snapshot=copy.deepcopy(item.edl_json or {}),
        status=RecordingEditOrder.Status.QUEUED,
        active_lock_key=f"item:{item.pk}",
        requested_by=completed_order.requested_by,
    )
    logger.warning(
        "recording_order_compensating_stop_publish_queued",
        extra={
            "event_type": "recording_order_compensating_stop_publish_queued",
            "order_id": compensating.pk,
            "source_order_id": completed_order.pk,
            "event_detail_id": detail.pk,
        },
    )
    return compensating


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

    if status == RecordingEditOrder.Status.SUCCEEDED:
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
    elif not isinstance(error_message, str):
        raise RecordingValidationError("error_message は文字列で指定してください。")

    ref = RecordingEditOrder.objects.values("review_item_id", "event_detail_id").filter(pk=order_id).first()
    if ref is None:
        raise RecordingNotFoundError("対象の確定指示が見つかりません。")

    lease_expired = False
    with transaction.atomic():
        detail = lock_event_detail(ref["event_detail_id"], include_deleted=True)
        item = RecordingReviewItem.objects.select_for_update().get(pk=ref["review_item_id"])
        order = RecordingEditOrder.objects.select_for_update().get(pk=order_id)

        if order.status != RecordingEditOrder.Status.RUNNING:
            logger.warning(
                "recording_order_complete_conflict",
                extra={
                    "event_type": "recording_order_complete_conflict",
                    "order_id": order.pk,
                    "event_detail_id": detail.pk,
                    "reason": "order_not_running",
                },
            )
            raise RecordingConflictError(
                "処理中（running）ではないオーダーは完了報告できません。",
                code="order_not_running",
                current_state={
                    "order_id": order.pk,
                    "status": order.status,
                    "target_revision": order.target_revision,
                },
            )

        if order.target_revision != target_revision:
            logger.warning(
                "recording_order_complete_conflict",
                extra={
                    "event_type": "recording_order_complete_conflict",
                    "order_id": order.pk,
                    "event_detail_id": detail.pk,
                    "reason": "target_revision_mismatch",
                },
            )
            raise RecordingConflictError(
                "target_revision が現在のオーダーと一致しません。",
                code="target_revision_mismatch",
                current_state={
                    "order_id": order.pk,
                    "status": order.status,
                    "target_revision": order.target_revision,
                },
            )

        now = timezone.now()
        if is_order_lease_expired(order, now=now):
            mark_order_lease_expired(
                order,
                now=now,
                error_message="ワーカーのリース期限（30分）を超過したため完了報告を拒否しました。",
                event_detail_id=detail.pk,
            )
            lease_expired = True
        else:
            if status == RecordingEditOrder.Status.SUCCEEDED:
                compensating_stop_needed = _apply_succeeded_order(
                    detail=detail,
                    item=item,
                    order=order,
                    result_video_id=result_video_id,
                    youtube_url=youtube_url,
                    youtube_draft_privacy=youtube_draft_privacy,
                    review_status=review_status,
                )
            else:
                order.error_message = error_message[:10000]
                compensating_stop_needed = False

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
            if compensating_stop_needed:
                _enqueue_compensating_stop_publish(detail=detail, item=item, completed_order=order)

    if lease_expired:
        raise RecordingConflictError(
            "リース期限（30分）を超過したオーダーは完了報告できません。",
            code="order_lease_expired",
            current_state={
                "order_id": order.pk,
                "status": order.status,
                "target_revision": order.target_revision,
            },
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
