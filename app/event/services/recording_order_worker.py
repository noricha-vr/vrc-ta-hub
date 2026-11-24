"""録画編集確定指示キューのワーカー側リース（claim）処理。"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from community.constants import RecordingPolicy
from event.models import EventDetail, RecordingEditOrder, RecordingReviewItem
from event.services.recording_ops_common import (
    ORDER_LEASE_TIMEOUT,
    RecordingValidationError,
    lock_event_detail,
    mark_order_lease_expired,
)

logger = logging.getLogger(__name__)


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


def _expire_stale_running_orders(*, now: Any) -> int:
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
            lock_event_detail(ref["event_detail_id"], include_deleted=True)
            RecordingReviewItem.objects.select_for_update().filter(pk=ref["review_item_id"]).first()
            order_qs = RecordingEditOrder.objects.filter(
                Q(started_at__lte=cutoff) | Q(started_at__isnull=True),
                pk=ref["id"],
                status=RecordingEditOrder.Status.RUNNING,
            )
            if use_skip_locked:
                order_qs = order_qs.select_for_update(skip_locked=True)
            elif use_select_for_update:
                order_qs = order_qs.select_for_update()
            order = order_qs.first()
            if order is None:
                continue
            mark_order_lease_expired(order, now=now, event_detail_id=order.event_detail_id)
            expired_count += 1
    return expired_count


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
