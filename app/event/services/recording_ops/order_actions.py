"""録画編集キューの再試行・取り消し・手動緊急停止アクション（Layer 3）。"""
from __future__ import annotations

import logging
from typing import Any

from django.db import transaction
from django.utils import timezone

from event.models import RecordingEditOrder, RecordingReviewItem

from .edl import EDLValidationError, validate_edl_v1
from .errors import (
    MAX_REVISION,
    RecordingConflictError,
    RecordingNotFoundError,
    RecordingValidationError,
    serialize_item_state,
    validate_positive_revision,
)
from .locks import is_recording_consent_valid, lock_event_detail
from .orders import (
    _can_create_manual_stop_publish,
    _create_order_locked,
    _ensure_order_consent,
    _raise_order_conflict,
    _sweep_active_orders,
)
from .stop_queue import item_has_unstopped_video
from .storage import ensure_recording_consent

logger = logging.getLogger(__name__)


def retry_edit_order(
    detail_id: int,
    order_id: int,
    *,
    expected_revision: int,
    requested_by: Any = None,
) -> RecordingEditOrder:
    """失敗（failed）状態の確定・停止オーダーを expected_revision 排他制御付きで再試行する。"""
    validate_positive_revision(expected_revision, "expected_revision")

    conflict_exc: RecordingConflictError | None = None
    new_order: RecordingEditOrder | None = None
    source_order_type = ""

    with transaction.atomic():
        detail = lock_event_detail(detail_id, include_deleted=True)
        item = RecordingReviewItem.objects.select_for_update().filter(event_detail=detail).first()
        if item is None:
            _raise_order_conflict(
                detail.pk,
                RecordingEditOrder.OrderType.APPLY_EDL,
                "proxy_not_ready",
                "プロキシ動画がまだ登録されていないため再試行できません。",
            )
        order = RecordingEditOrder.objects.select_for_update().filter(pk=order_id, review_item=item).first()
        if order is None:
            raise RecordingNotFoundError("対象の再試行オーダーが見つかりません。")
        source_order_type = order.order_type

        if detail.deleted_at is not None and (
            order.order_type != RecordingEditOrder.OrderType.STOP_PUBLISH
            or not _can_create_manual_stop_publish(item)
        ):
            raise RecordingNotFoundError("対象の発表が見つかりません。")

        _ensure_order_consent(detail, item, order_type=order.order_type)

        if expected_revision != item.edl_revision:
            _raise_order_conflict(
                detail.pk,
                order.order_type,
                "revision_conflict",
                "expected_revision が現在の EDL リビジョンと一致しません。",
                item,
            )

        if order.status == RecordingEditOrder.Status.RUNNING:
            _raise_order_conflict(
                detail.pk,
                order.order_type,
                "order_running",
                "処理中（running）のオーダーは再試行できません。",
                item,
            )
        if order.status != RecordingEditOrder.Status.FAILED:
            _raise_order_conflict(
                detail.pk,
                order.order_type,
                "order_not_retryable",
                "失敗（failed）状態のオーダーのみ再試行できます。",
                item,
            )

        if (
            RecordingEditOrder.objects.filter(review_item=item, pk__gt=order.pk)
            .exclude(status=RecordingEditOrder.Status.CANCELED)
            .exists()
        ):
            _raise_order_conflict(
                detail.pk,
                order.order_type,
                "stale_order",
                "より新しい確定指示が存在するため、この失敗オーダーは再試行できません。",
                item,
            )

        if (
            order.order_type == RecordingEditOrder.OrderType.STOP_PUBLISH
            and not item_has_unstopped_video(item)
        ):
            _raise_order_conflict(
                detail.pk,
                order.order_type,
                "nothing_to_stop",
                "すでに非公開確定済み、または停止対象の公開動画が存在しません。",
                item,
            )

        now = timezone.now()
        if _sweep_active_orders(item=item, now=now, detail_id_for_log=detail.pk):
            _raise_order_conflict(
                detail.pk,
                order.order_type,
                "active_order_exists",
                "未完了の確定指示が既に存在するため再試行できません。",
                item,
            )

        validated_snapshot: dict[str, Any] | None = None
        if (
            order.order_type == RecordingEditOrder.OrderType.APPLY_EDL
            and isinstance(order.edl_snapshot, dict)
            and order.edl_snapshot
        ):
            try:
                validated_snapshot = validate_edl_v1(order.edl_snapshot)
            except EDLValidationError as exc:
                raise RecordingValidationError(str(exc)) from exc

        result = _create_order_locked(
            detail=detail,
            item=item,
            expected_revision=expected_revision,
            order_type=order.order_type,
            validated_incoming_edl=validated_snapshot,
            requested_by=requested_by,
        )
        if isinstance(result, RecordingConflictError):
            conflict_exc = result
        else:
            new_order = result

    if conflict_exc is not None:
        raise conflict_exc
    assert new_order is not None
    logger.info(
        "recording_order_retried",
        extra={
            "event_type": "recording_order_retried",
            "source_order_id": order_id,
            "order_id": new_order.pk,
            "event_detail_id": detail_id,
            "order_type": source_order_type,
            "base_revision": new_order.base_revision,
            "target_revision": new_order.target_revision,
        },
    )
    return new_order


def cancel_edit_order(
    detail_id: int,
    order_id: int,
    *,
    expected_revision: int,
    requested_by: Any = None,
) -> RecordingEditOrder:
    """待機中（queued）または失敗（failed）のオーダーを取り消し、running 中は 409 で遮断する。"""
    validate_positive_revision(expected_revision, "expected_revision")

    with transaction.atomic():
        detail = lock_event_detail(detail_id, include_deleted=True)
        item = RecordingReviewItem.objects.select_for_update().filter(event_detail=detail).first()
        if item is None:
            _raise_order_conflict(
                detail.pk,
                RecordingEditOrder.OrderType.APPLY_EDL,
                "proxy_not_ready",
                "プロキシ動画がまだ登録されていないため取り消しできません。",
            )
        order = RecordingEditOrder.objects.select_for_update().filter(pk=order_id, review_item=item).first()
        if order is None:
            raise RecordingNotFoundError("対象の取り消しオーダーが見つかりません。")
        if detail.deleted_at is not None:
            raise RecordingNotFoundError("対象の発表が見つかりません。")

        if order.order_type == RecordingEditOrder.OrderType.STOP_PUBLISH:
            if not is_recording_consent_valid(detail) or item.compensating_stop_pending:
                _raise_order_conflict(
                    detail.pk,
                    order.order_type,
                    "stop_publish_required",
                    "撮影許可が無効または補償停止が必要な状態のため、公開停止指示は取り消せません。",
                    item,
                )
        else:
            ensure_recording_consent(detail, action="cancel_order")

        if expected_revision != item.edl_revision:
            _raise_order_conflict(
                detail.pk,
                order.order_type,
                "revision_conflict",
                "expected_revision が現在の EDL リビジョンと一致しません。",
                item,
            )
        if order.status == RecordingEditOrder.Status.RUNNING:
            _raise_order_conflict(
                detail.pk,
                order.order_type,
                "order_running",
                "処理中（running）のオーダーは取り消しできません。",
                item,
            )
        if order.status not in (
            RecordingEditOrder.Status.QUEUED,
            RecordingEditOrder.Status.FAILED,
        ):
            _raise_order_conflict(
                detail.pk,
                order.order_type,
                "order_not_cancelable",
                "待機中（queued）または失敗（failed）のオーダーのみ取り消しできます。",
                item,
            )

        if (
            order.status == RecordingEditOrder.Status.FAILED
            and RecordingEditOrder.objects.filter(review_item=item, pk__gt=order.pk)
            .exclude(status=RecordingEditOrder.Status.CANCELED)
            .exists()
        ):
            _raise_order_conflict(
                detail.pk,
                order.order_type,
                "stale_order",
                "より新しい確定指示が存在するため取り消せません。",
                item,
            )

        if item.edl_revision >= MAX_REVISION:
            raise RecordingConflictError(
                "EDL リビジョン上限に達しています。",
                code="revision_limit_exceeded",
                current_state=serialize_item_state(item),
            )

        now = timezone.now()
        previous_status = order.status
        actor_label = (
            requested_by.display_label
            if getattr(requested_by, "is_authenticated", False) and hasattr(requested_by, "display_label")
            else "運営スタッフ"
        )
        cancel_note = f"{actor_label}により取り消されました。"
        order.status = RecordingEditOrder.Status.CANCELED
        order.active_lock_key = None
        order.completed_at = now
        order.error_message = (
            f"{cancel_note}（元のエラー: {order.error_message}）" if order.error_message else cancel_note
        )[:10000]
        order.save(
            update_fields=["status", "active_lock_key", "completed_at", "error_message", "updated_at"]
        )

        item.edl_revision += 1
        item.save(update_fields=["edl_revision", "updated_at"])

    logger.info(
        "recording_order_canceled_by_staff",
        extra={
            "event_type": "recording_order_canceled_by_staff",
            "order_id": order.pk,
            "previous_status": previous_status,
            "event_detail_id": detail_id,
            "order_type": order.order_type,
            "edl_revision": item.edl_revision,
        },
    )
    return order


def enqueue_manual_stop_publish(
    detail_id: int,
    *,
    expected_revision: int,
    requested_by: Any = None,
) -> RecordingEditOrder:
    """公開中・限定公開中の発表に対し、公開ロック中でも緊急停止（stop_publish）を起票する。"""
    validate_positive_revision(expected_revision, "expected_revision")

    conflict_exc: RecordingConflictError | None = None
    order: RecordingEditOrder | None = None

    with transaction.atomic():
        detail = lock_event_detail(detail_id, include_deleted=True)
        item = RecordingReviewItem.objects.select_for_update().filter(event_detail=detail).first()
        if detail.deleted_at is not None and not _can_create_manual_stop_publish(item):
            raise RecordingNotFoundError("対象の発表が見つかりません。")
        _ensure_order_consent(detail, item, order_type=RecordingEditOrder.OrderType.STOP_PUBLISH)

        if item is None:
            _raise_order_conflict(
                detail.pk,
                RecordingEditOrder.OrderType.STOP_PUBLISH,
                "proxy_not_ready",
                "録画アイテムがまだ登録されていないため緊急停止を起票できません。",
            )
        if not item_has_unstopped_video(item):
            _raise_order_conflict(
                detail.pk,
                RecordingEditOrder.OrderType.STOP_PUBLISH,
                "nothing_to_stop",
                "すでに非公開確定済み、または停止対象の公開動画が存在しません。",
                item,
            )

        result = _create_order_locked(
            detail=detail,
            item=item,
            expected_revision=expected_revision,
            order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
            validated_incoming_edl=None,
            requested_by=requested_by,
        )
        if isinstance(result, RecordingConflictError):
            conflict_exc = result
        else:
            order = result

    if conflict_exc is not None:
        raise conflict_exc
    assert order is not None
    return order
