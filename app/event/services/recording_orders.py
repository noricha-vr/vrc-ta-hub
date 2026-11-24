"""EDL v1 確定指示キュー（RecordingEditOrder）の作成・リース・完了処理サービス。"""
from __future__ import annotations

import copy
import logging
from collections.abc import Sequence
from typing import Any

from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from community.constants import RecordingPolicy
from event.models import EventDetail, RecordingEditOrder, RecordingReviewItem
from event.services.edl import EDLValidationError, validate_edl_v1
from event.services.recording_ops_common import (
    RecordingConflictError,
    RecordingNotFoundError,
    RecordingValidationError,
    lock_event_detail,
    serialize_item_state,
    validate_positive_revision,
    validate_safe_youtube_url,
    validate_video_id,
)
from event.services.recording_storage import ensure_recording_consent
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


def _ensure_order_consent(
    detail: EventDetail,
    item: RecordingReviewItem | None,
    *,
    order_type: str,
) -> None:
    if order_type == RecordingEditOrder.OrderType.APPLY_EDL:
        ensure_recording_consent(detail, action="create_order")
        return

    # stop_publish は、同意が public のままの場合に加えて、既に公開/下書き動画が
    # 存在し緊急停止が必要なケースのみ許可する（未同意の発表への不要な指示作成を遮断）。
    has_existing_video = item is not None and (
        bool(item.youtube_draft_video_id)
        or item.review_status
        in (
            RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
            RecordingReviewItem.ReviewStatus.PUBLISHED,
        )
    )
    if not has_existing_video:
        ensure_recording_consent(detail, action="stop_publish")


def create_edit_order(
    detail_id: int,
    *,
    expected_revision: int,
    order_type: str = RecordingEditOrder.OrderType.APPLY_EDL,
    edl_json: dict[str, Any] | None = None,
    requested_by: Any = None,
) -> RecordingEditOrder:
    """expected_revision を照合して確定指示キュー（RecordingEditOrder）を作成する。"""
    validate_positive_revision(expected_revision, "expected_revision")
    if order_type not in (
        RecordingEditOrder.OrderType.APPLY_EDL,
        RecordingEditOrder.OrderType.STOP_PUBLISH,
    ):
        raise RecordingValidationError("order_type は apply_edl または stop_publish を指定してください。")

    validated_incoming_edl: dict[str, Any] | None = None
    if edl_json is not None:
        try:
            validated_incoming_edl = validate_edl_v1(edl_json)
        except EDLValidationError as exc:
            raise RecordingValidationError(str(exc)) from exc

    with transaction.atomic():
        detail = lock_event_detail(detail_id)
        item = RecordingReviewItem.objects.select_for_update().filter(event_detail=detail).first()
        _ensure_order_consent(detail, item, order_type=order_type)

        if item is None:
            logger.warning(
                "recording_order_conflict",
                extra={
                    "event_type": "recording_order_conflict",
                    "event_detail_id": detail.pk,
                    "order_type": order_type,
                    "reason": "proxy_not_ready",
                },
            )
            raise RecordingConflictError(
                "プロキシ動画がまだ登録されていないため確定指示を作成できません。",
                code="proxy_not_ready",
            )

        now = timezone.now()
        if order_type == RecordingEditOrder.OrderType.APPLY_EDL:
            if item.review_status == RecordingReviewItem.ReviewStatus.EXPIRED or (
                item.proxy_expires_at is not None and item.proxy_expires_at <= now
            ):
                logger.warning(
                    "recording_order_conflict",
                    extra={
                        "event_type": "recording_order_conflict",
                        "event_detail_id": detail.pk,
                        "order_type": order_type,
                        "reason": "proxy_expired",
                    },
                )
                raise RecordingConflictError(
                    "プロキシ動画の保持期限が切れています。",
                    code="proxy_expired",
                    current_state=serialize_item_state(item),
                )
            if item.is_public_locked:
                logger.warning(
                    "recording_order_conflict",
                    extra={
                        "event_type": "recording_order_conflict",
                        "event_detail_id": detail.pk,
                        "order_type": order_type,
                        "reason": "public_locked",
                    },
                )
                raise RecordingConflictError(
                    "公開済みロック中のため apply_edl の確定指示は作成できません。",
                    code="public_locked",
                    current_state=serialize_item_state(item),
                )

        if expected_revision != item.edl_revision:
            logger.warning(
                "recording_order_conflict",
                extra={
                    "event_type": "recording_order_conflict",
                    "event_detail_id": detail.pk,
                    "order_type": order_type,
                    "reason": "revision_conflict",
                },
            )
            raise RecordingConflictError(
                "expected_revision が現在の EDL リビジョンと一致しません。",
                code="revision_conflict",
                current_state=serialize_item_state(item),
            )

        if RecordingEditOrder.objects.filter(
            review_item=item,
            status__in=RecordingEditOrder.ACTIVE_STATUSES,
        ).exists():
            logger.warning(
                "recording_order_conflict",
                extra={
                    "event_type": "recording_order_conflict",
                    "event_detail_id": detail.pk,
                    "order_type": order_type,
                    "reason": "active_order_exists",
                },
            )
            raise RecordingConflictError(
                "未完了の確定指示が既に存在します。",
                code="active_order_exists",
                current_state=serialize_item_state(item),
            )

        if order_type == RecordingEditOrder.OrderType.APPLY_EDL:
            if validated_incoming_edl is not None:
                snapshot_edl = validated_incoming_edl
            else:
                try:
                    snapshot_edl = validate_edl_v1(item.edl_json)
                except EDLValidationError as exc:
                    raise RecordingValidationError(str(exc)) from exc
        else:
            snapshot_edl = (
                validated_incoming_edl
                if validated_incoming_edl is not None
                else copy.deepcopy(item.edl_json or {})
            )

        base_revision = item.edl_revision
        target_revision = base_revision + 1
        item.edl_revision = target_revision
        item_update_fields = ["edl_revision", "updated_at"]
        if order_type == RecordingEditOrder.OrderType.APPLY_EDL and validated_incoming_edl is not None:
            item.edl_json = copy.deepcopy(validated_incoming_edl)
            item_update_fields.append("edl_json")
        item.save(update_fields=item_update_fields)

        try:
            with transaction.atomic():
                order = RecordingEditOrder.objects.create(
                    review_item=item,
                    event_detail=detail,
                    order_type=order_type,
                    base_revision=base_revision,
                    target_revision=target_revision,
                    edl_snapshot=snapshot_edl,
                    status=RecordingEditOrder.Status.QUEUED,
                    active_lock_key=f"item:{item.pk}",
                    requested_by=requested_by if getattr(requested_by, "is_authenticated", False) else None,
                )
        except IntegrityError as exc:
            logger.warning(
                "recording_order_conflict",
                extra={
                    "event_type": "recording_order_conflict",
                    "event_detail_id": detail.pk,
                    "order_type": order_type,
                    "reason": "active_order_exists",
                },
            )
            raise RecordingConflictError(
                "未完了の確定指示が既に存在します。",
                code="active_order_exists",
                current_state=serialize_item_state(item),
            ) from exc

    logger.info(
        "recording_order_created",
        extra={
            "event_type": "recording_order_created",
            "order_id": order.pk,
            "event_detail_id": detail.pk,
            "order_type": order.order_type,
            "base_revision": order.base_revision,
            "target_revision": order.target_revision,
        },
    )
    return order


def claim_next_edit_order(*, order_types: Sequence[str] | None = None) -> RecordingEditOrder | None:
    """最古の queued オーダーを 1 件リース（running 遷移）する。"""
    if order_types is not None:
        valid_types = {choice for choice, _ in RecordingEditOrder.OrderType.choices}
        if any(t not in valid_types for t in order_types):
            raise RecordingValidationError("order_types に無効な指示種別が含まれています。")

    with transaction.atomic():
        qs = RecordingEditOrder.objects.filter(status=RecordingEditOrder.Status.QUEUED)
        if order_types:
            qs = qs.filter(order_type__in=list(order_types))
        qs = qs.select_related("review_item").order_by("created_at", "pk")
        if connection.features.has_select_for_update_skip_locked:
            qs = qs.select_for_update(skip_locked=True)
        elif connection.features.has_select_for_update:
            qs = qs.select_for_update()

        order = qs.first()
        if order is None:
            return None

        order.status = RecordingEditOrder.Status.RUNNING
        order.started_at = timezone.now()
        order.active_lock_key = f"item:{order.review_item_id}"
        order.save(update_fields=["status", "started_at", "active_lock_key", "updated_at"])

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
