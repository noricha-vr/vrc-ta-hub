"""録画編集確定指示キューのワーカー側リース（claim）および完了報告処理。"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from django.db import connection, transaction
from django.utils import timezone

from community.constants import RecordingPolicy
from event.models import EventDetail, RecordingEditOrder, RecordingReviewItem
from event.services.recording_ops_common import (
    RecordingConflictError,
    RecordingNotFoundError,
    RecordingValidationError,
    lock_event_detail,
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


def _claim_invalidation_reason(
    detail: EventDetail | None,
    item: RecordingReviewItem,
    order: RecordingEditOrder,
    *,
    now: Any,
) -> str | None:
    """claim 時点でオーダーが無効化されている場合はキャンセル理由を返す。"""
    if detail is None:
        return "detail_not_found"
    if order.order_type != RecordingEditOrder.OrderType.APPLY_EDL:
        return None
    if detail.deleted_at is not None:
        return "detail_deleted"
    if detail.recording_policy != RecordingPolicy.PUBLIC or not detail.event.community.recording_allowed:
        return "consent_revoked"
    if item.is_public_locked:
        return "public_locked"
    if item.review_status == RecordingReviewItem.ReviewStatus.EXPIRED or (
        item.proxy_expires_at is not None and item.proxy_expires_at <= now
    ):
        return "proxy_expired"
    return None


def claim_next_edit_order(*, order_types: Sequence[str] | None = None) -> RecordingEditOrder | None:
    """最古の有効な queued オーダーを 1 件リース（running 遷移）する。

    同意撤回・論理削除・期限切れ等で無効化された apply_edl オーダーは
    running にせず canceled へ遷移させて排他キーを解放し、次の待機オーダーへ進む。
    """
    if order_types is not None:
        valid_types = {choice for choice, _ in RecordingEditOrder.OrderType.choices}
        if any(t not in valid_types for t in order_types):
            raise RecordingValidationError("order_types に無効な指示種別が含まれています。")

    base_qs = RecordingEditOrder.objects.filter(status=RecordingEditOrder.Status.QUEUED)
    if order_types:
        base_qs = base_qs.filter(order_type__in=list(order_types))

    seen_ids: set[int] = set()
    use_skip_locked = connection.features.has_select_for_update_skip_locked
    use_select_for_update = connection.features.has_select_for_update

    while True:
        candidates = list(
            base_qs.exclude(pk__in=seen_ids)
            .order_by("created_at", "pk")
            .values("id", "review_item_id", "event_detail_id")[:20]
        )
        if not candidates:
            return None

        for candidate in candidates:
            candidate_id = candidate["id"]
            seen_ids.add(candidate_id)

            with transaction.atomic():
                # 他サービスと同じく EventDetail -> RecordingReviewItem -> RecordingEditOrder の順でロックする。
                detail = (
                    EventDetail.all_objects.select_for_update(of=("self",))
                    .select_related("event__community")
                    .filter(pk=candidate["event_detail_id"])
                    .first()
                )
                item = (
                    RecordingReviewItem.objects.select_for_update()
                    .filter(pk=candidate["review_item_id"])
                    .first()
                )
                if item is None:
                    continue

                order_qs = RecordingEditOrder.objects.filter(
                    pk=candidate_id,
                    status=RecordingEditOrder.Status.QUEUED,
                )
                if use_skip_locked:
                    order_qs = order_qs.select_for_update(skip_locked=True)
                elif use_select_for_update:
                    order_qs = order_qs.select_for_update()

                order = order_qs.first()
                if order is None:
                    continue

                now = timezone.now()
                invalid_reason = _claim_invalidation_reason(detail, item, order, now=now)
                if invalid_reason is not None:
                    order.status = RecordingEditOrder.Status.CANCELED
                    order.active_lock_key = None
                    order.completed_at = now
                    order.error_message = f"無効化条件（{invalid_reason}）により claim 時に自動キャンセルしました。"
                    order.save(
                        update_fields=[
                            "status",
                            "active_lock_key",
                            "completed_at",
                            "error_message",
                            "updated_at",
                        ]
                    )
                    logger.warning(
                        "recording_order_canceled",
                        extra={
                            "event_type": "recording_order_canceled",
                            "order_id": order.pk,
                            "event_detail_id": order.event_detail_id,
                            "order_type": order.order_type,
                            "reason": invalid_reason,
                        },
                    )
                    continue

                order.status = RecordingEditOrder.Status.RUNNING
                order.started_at = now
                order.active_lock_key = f"item:{order.review_item_id}"
                order.save(update_fields=["status", "started_at", "active_lock_key", "updated_at"])
                order.review_item = item

            logger.info(
                "recording_order_claimed",
                extra={
                    "event_type": "recording_order_claimed",
                    "order_id": order.pk,
                    "event_detail_id": order.event_detail_id,
                    "order_type": order.order_type,
                    "target_revision": order.target_revision,
                },
            )
            return order


def _derive_review_status_on_success(
    *,
    order_type: str,
    privacy: str,
    requested_review_status: str,
) -> str:
    if order_type == RecordingEditOrder.OrderType.STOP_PUBLISH:
        return requested_review_status or RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
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
        order.status = status
        order.active_lock_key = None
        order.completed_at = now

        if status == RecordingEditOrder.Status.SUCCEEDED:
            order.result_video_id = result_video_id
            order.error_message = ""

            item_fields = ["review_status", "updated_at"]
            if order.order_type == RecordingEditOrder.OrderType.STOP_PUBLISH:
                item.youtube_draft_privacy = youtube_draft_privacy or RecordingReviewItem.YouTubePrivacy.PRIVATE
                item_fields.append("youtube_draft_privacy")
                item.review_status = _derive_review_status_on_success(
                    order_type=order.order_type,
                    privacy=item.youtube_draft_privacy,
                    requested_review_status=review_status,
                )
            else:
                if result_video_id:
                    item.youtube_draft_video_id = result_video_id
                    item_fields.append("youtube_draft_video_id")
                if youtube_draft_privacy:
                    item.youtube_draft_privacy = youtube_draft_privacy
                    item_fields.append("youtube_draft_privacy")
                item.review_status = _derive_review_status_on_success(
                    order_type=order.order_type,
                    privacy=item.youtube_draft_privacy,
                    requested_review_status=review_status,
                )

                if youtube_url:
                    can_update_detail = (
                        detail.deleted_at is None
                        and detail.recording_policy == RecordingPolicy.PUBLIC
                        and bool(detail.event.community.recording_allowed)
                    )
                    if can_update_detail and youtube_url != (detail.youtube_url or ""):
                        detail.youtube_url = youtube_url
                        detail.save(update_fields=["youtube_url", "updated_at"])
                    elif not can_update_detail:
                        logger.warning(
                            "recording_order_youtube_url_skipped",
                            extra={
                                "event_type": "recording_order_youtube_url_skipped",
                                "order_id": order.pk,
                                "event_detail_id": detail.pk,
                                "reason": "consent_or_deleted",
                            },
                        )

            item.save(update_fields=list(dict.fromkeys(item_fields)))
        else:
            order.error_message = error_message[:10000]

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
