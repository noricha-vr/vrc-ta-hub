"""録画編集確定指示キューのワーカー側リース（claim）処理（Layer 3）。"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from django.db import connection, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from event.models import EventDetail, RecordingEditOrder, RecordingReviewItem

from .errors import ORDER_LEASE_TIMEOUT, RecordingValidationError
from .locks import is_recording_consent_valid, lock_event_detail_or_none
from .stop_queue import (
    clear_detail_youtube_url,
    enqueue_compensating_stop_publish,
    item_has_unstopped_video,
    mark_order_lease_expired,
)

logger = logging.getLogger(__name__)


def _claim_invalidation_reason(
    detail: EventDetail | None,
    item: RecordingReviewItem,
    order: RecordingEditOrder,
    *,
    now: datetime,
) -> str | None:
    """claim 時点でオーダーが無効化されている場合はキャンセル理由を返す。"""
    if order.order_type != RecordingEditOrder.OrderType.APPLY_EDL:
        return None
    if detail is None:
        return "detail_not_found"
    if detail.deleted_at is not None:
        return "detail_deleted"
    if not is_recording_consent_valid(detail):
        return "consent_revoked"
    if item.is_public_locked:
        return "public_locked"
    if item.review_status == RecordingReviewItem.ReviewStatus.EXPIRED or (
        item.proxy_expires_at is not None and item.proxy_expires_at <= now
    ):
        return "proxy_expired"
    if item.compensating_stop_pending:
        return "compensating_stop_pending"
    return None


def _select_order_for_update(
    qs: QuerySet[RecordingEditOrder],
    *,
    use_skip_locked: bool,
    use_select_for_update: bool,
) -> RecordingEditOrder | None:
    if use_skip_locked:
        qs = qs.select_for_update(skip_locked=True)
    elif use_select_for_update:
        qs = qs.select_for_update()
    return qs.first()


def _handle_expired_order_compensation(
    *,
    detail: EventDetail | None,
    item: RecordingReviewItem | None,
    order: RecordingEditOrder,
) -> None:
    if (
        order.order_type != RecordingEditOrder.OrderType.APPLY_EDL
        or item is None
        or (detail is not None and is_recording_consent_valid(detail) and not item.compensating_stop_pending)
    ):
        return
    clear_detail_youtube_url(detail)
    has_active = RecordingEditOrder.objects.filter(
        review_item=item,
        status__in=RecordingEditOrder.ACTIVE_STATUSES,
    ).exists()
    if not has_active and item_has_unstopped_video(item):
        enqueue_compensating_stop_publish(
            detail=detail,
            item=item,
            requested_by=order.requested_by,
            source_order_id=order.pk,
        )
    elif not has_active and item.compensating_stop_pending:
        item.compensating_stop_pending = False
        item.save(update_fields=["compensating_stop_pending", "updated_at"])


def _expire_stale_running_orders(*, now: datetime) -> int:
    """リース期限を超過した running オーダーを failed に遷移させて排他キーを解放する。"""
    cutoff = now - ORDER_LEASE_TIMEOUT
    stale_refs = list(
        RecordingEditOrder.objects.filter(
            Q(started_at__lte=cutoff) | Q(started_at__isnull=True),
            status=RecordingEditOrder.Status.RUNNING,
        )
        .order_by("started_at", "pk")
        .values("id", "review_item_id", "event_detail_id")[:20]
    )
    expired_count = 0
    use_skip_locked = connection.features.has_select_for_update_skip_locked
    use_select_for_update = connection.features.has_select_for_update

    for ref in stale_refs:
        with transaction.atomic():
            detail = (
                lock_event_detail_or_none(ref["event_detail_id"], include_deleted=True)
                if ref["event_detail_id"] is not None
                else None
            )
            item = RecordingReviewItem.objects.select_for_update().filter(pk=ref["review_item_id"]).first()
            order = _select_order_for_update(
                RecordingEditOrder.objects.filter(
                    Q(started_at__lte=cutoff) | Q(started_at__isnull=True),
                    pk=ref["id"],
                    status=RecordingEditOrder.Status.RUNNING,
                ),
                use_skip_locked=use_skip_locked,
                use_select_for_update=use_select_for_update,
            )
            if order is None:
                continue
            mark_order_lease_expired(order, now=now, event_detail_id=order.event_detail_id)
            _handle_expired_order_compensation(detail=detail, item=item, order=order)
            expired_count += 1
    return expired_count


def _cancel_invalidated_claim_order(
    *,
    detail: EventDetail | None,
    item: RecordingReviewItem,
    order: RecordingEditOrder,
    invalid_reason: str,
    now: datetime,
) -> None:
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
    if (
        invalid_reason
        in ("detail_not_found", "detail_deleted", "consent_revoked", "compensating_stop_pending")
        or item.compensating_stop_pending
    ):
        if item_has_unstopped_video(item):
            clear_detail_youtube_url(detail)
            enqueue_compensating_stop_publish(
                detail=detail,
                item=item,
                requested_by=order.requested_by,
                source_order_id=order.pk,
            )
        elif item.compensating_stop_pending:
            item.compensating_stop_pending = False
            item.save(update_fields=["compensating_stop_pending", "updated_at"])


def claim_next_edit_order(*, order_types: Sequence[str] | None = None) -> RecordingEditOrder | None:
    """最古の有効な queued オーダーを 1 件リース（running 遷移）する。

    同意撤回・論理削除・期限切れ等で無効化された apply_edl オーダーは
    running にせず canceled へ遷移させて排他キーを解放し、次の待機オーダーへ進む。
    """
    if order_types is not None:
        valid_types = {choice for choice, _ in RecordingEditOrder.OrderType.choices}
        if any(t not in valid_types for t in order_types):
            raise RecordingValidationError("order_types に無効な指示種別が含まれています。")

    _expire_stale_running_orders(now=timezone.now())

    base_qs: QuerySet[Any] = RecordingEditOrder.objects.filter(status=RecordingEditOrder.Status.QUEUED)
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
                # 統一ロック順: Community -> EventDetail -> RecordingReviewItem -> RecordingEditOrder
                detail = (
                    lock_event_detail_or_none(candidate["event_detail_id"], include_deleted=True)
                    if candidate["event_detail_id"] is not None
                    else None
                )
                item = (
                    RecordingReviewItem.objects.select_for_update()
                    .filter(pk=candidate["review_item_id"])
                    .first()
                )
                if item is None:
                    continue

                order = _select_order_for_update(
                    RecordingEditOrder.objects.filter(
                        pk=candidate_id,
                        status=RecordingEditOrder.Status.QUEUED,
                    ),
                    use_skip_locked=use_skip_locked,
                    use_select_for_update=use_select_for_update,
                )
                if order is None:
                    continue

                now = timezone.now()
                invalid_reason = _claim_invalidation_reason(detail, item, order, now=now)
                if invalid_reason is not None:
                    _cancel_invalidated_claim_order(
                        detail=detail,
                        item=item,
                        order=order,
                        invalid_reason=invalid_reason,
                        now=now,
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
