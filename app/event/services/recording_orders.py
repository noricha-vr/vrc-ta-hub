"""EDL v1 確定指示キュー（RecordingEditOrder）の作成サービス。"""
from __future__ import annotations

import copy
import logging
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from event.models import EventDetail, RecordingEditOrder, RecordingReviewItem
from event.services.edl import EDLValidationError, validate_edl_v1
from event.services.recording_ops_common import (
    RecordingConflictError,
    RecordingValidationError,
    lock_event_detail,
    serialize_item_state,
    validate_positive_revision,
)
from event.services.recording_storage import ensure_recording_consent

logger = logging.getLogger(__name__)


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
                registered_bounds = (
                    item.edl_json.get("safe_bounds") if isinstance(item.edl_json, dict) else None
                )
                if isinstance(registered_bounds, dict):
                    incoming_bounds = validated_incoming_edl["safe_bounds"]
                    if (
                        float(incoming_bounds["min_sec"]) != float(registered_bounds["min_sec"])
                        or float(incoming_bounds["max_sec"]) != float(registered_bounds["max_sec"])
                    ):
                        raise RecordingValidationError("確定操作では登録済みの safe_bounds を変更できません。")
                if item.proxy_duration_sec is not None and (
                    float(validated_incoming_edl["safe_bounds"]["max_sec"]) > item.proxy_duration_sec
                ):
                    raise RecordingValidationError("safe_bounds.max_sec がプロキシ再生時間を超えています。")
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
