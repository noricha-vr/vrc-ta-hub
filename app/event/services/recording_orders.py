"""EDL v1 確定指示キュー（RecordingEditOrder）の作成サービス。"""
from __future__ import annotations

import copy
import logging
from datetime import datetime
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from community.models import Community
from event.models import Event, EventDetail, RecordingEditOrder, RecordingReviewItem
from event.services.edl import EDLValidationError, validate_edl_v1
from event.services.recording_ops_common import (
    MAX_REVISION,
    RecordingConflictError,
    RecordingNotFoundError,
    RecordingValidationError,
    add_pending_stop_video_id,
    cancel_queued_apply_edl_and_compensate,
    is_order_lease_expired,
    is_recording_consent_valid,
    lock_event_detail,
    mark_order_lease_expired,
    next_stop_target_video_id,
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
        is_stop_publish = order_type == RecordingEditOrder.OrderType.STOP_PUBLISH
        detail = lock_event_detail(detail_id, include_deleted=is_stop_publish)
        item = RecordingReviewItem.objects.select_for_update().filter(event_detail=detail).first()
        if detail.deleted_at is not None:
            has_existing_video = item is not None and (
                bool(item.youtube_draft_video_id)
                or item.review_status
                in (
                    RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
                    RecordingReviewItem.ReviewStatus.PUBLISHED,
                )
            )
            if not has_existing_video:
                raise RecordingNotFoundError("対象の発表が見つかりません。")
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
        if item.edl_revision >= MAX_REVISION:
            raise RecordingConflictError(
                "EDL リビジョン上限に達しています。",
                code="revision_limit_exceeded",
                current_state=serialize_item_state(item),
            )

        active_orders = list(
            RecordingEditOrder.objects.select_for_update().filter(
                review_item=item,
                status__in=RecordingEditOrder.ACTIVE_STATUSES,
            )
        )
        still_active = False
        for active_order in active_orders:
            if is_order_lease_expired(active_order, now=now):
                mark_order_lease_expired(active_order, now=now, event_detail_id=detail.pk)
            else:
                still_active = True

        if still_active:
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
        stop_target_video_id = ""
        if order_type == RecordingEditOrder.OrderType.STOP_PUBLISH:
            stop_target_video_id = next_stop_target_video_id(item)
            if stop_target_video_id and add_pending_stop_video_id(item, stop_target_video_id):
                item_update_fields.append("pending_stop_video_ids")
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
                    target_video_id=stop_target_video_id,
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


def _compensate_revoked_item(
    *,
    detail: EventDetail | None,
    item: RecordingReviewItem,
    now: datetime,
    target_detail_for_order: EventDetail | None = None,
) -> bool:
    """同意撤回・論理削除・物理削除された項目について公開 URL を消し、未完了 apply_edl 取消と停止予約を行う。"""
    if detail is not None and detail.youtube_url:
        detail.youtube_url = None
        detail.save(update_fields=["youtube_url", "updated_at"])
    return cancel_queued_apply_edl_and_compensate(
        detail=detail,
        item=item,
        now=now,
        target_detail_for_order=target_detail_for_order,
    )


def revoke_recording_for_detail(detail_id: int) -> bool:
    """発表単位の同意撤回・論理削除に合わせて、録画停止を予約する（EventDetail -> Item -> Order 順でロック）。"""
    with transaction.atomic():
        detail = (
            EventDetail.all_objects.select_for_update(of=("self",))
            .select_related("event__community")
            .filter(pk=detail_id)
            .first()
        )
        if detail is None or is_recording_consent_valid(detail):
            return False
        item = RecordingReviewItem.objects.select_for_update().filter(event_detail=detail).first()
        if item is None:
            return False
        return _compensate_revoked_item(
            detail=detail,
            item=item,
            now=timezone.now(),
            target_detail_for_order=detail,
        )


def revoke_recording_on_detail_delete(detail_id: int) -> bool:
    """発表または親イベントの物理削除直前に、撮影成果物の停止予約を切り離して保全する。"""
    with transaction.atomic():
        item = RecordingReviewItem.objects.select_for_update().filter(event_detail_id=detail_id).first()
        if item is None:
            return False
        return _compensate_revoked_item(
            detail=None,
            item=item,
            now=timezone.now(),
            target_detail_for_order=None,
        )


def revoke_recording_for_community(community_id: int) -> int:
    """集会の録画許可撤回に合わせて、配下発表の録画停止を一括予約する（Community -> EventDetail -> Item -> Order 順でロック）。"""
    queued_count = 0
    with transaction.atomic():
        community = Community.objects.select_for_update().filter(pk=community_id).first()
        if community is None or community.recording_allowed:
            return 0
        detail_ids = list(
            RecordingReviewItem.objects.filter(event_detail__event__community_id=community_id)
            .order_by("event_detail_id")
            .values_list("event_detail_id", flat=True)
        )
        now = timezone.now()
        for detail_id in detail_ids:
            detail = (
                EventDetail.all_objects.select_for_update(of=("self",))
                .select_related("event__community")
                .filter(pk=detail_id)
                .first()
            )
            if detail is None or is_recording_consent_valid(detail):
                continue
            item = RecordingReviewItem.objects.select_for_update().filter(event_detail=detail).first()
            if item is None:
                continue
            if _compensate_revoked_item(
                detail=detail,
                item=item,
                now=now,
                target_detail_for_order=detail,
            ):
                queued_count += 1
    return queued_count


def revoke_recording_for_event(event_id: int) -> int:
    """イベントの所属集会変更等で録画不許可となった配下発表の停止予約を行う（Community -> EventDetail -> Item -> Order 順）。"""
    community_id = Event.objects.filter(pk=event_id).values_list("community_id", flat=True).first()
    if community_id is None:
        return 0
    queued_count = 0
    with transaction.atomic():
        Community.objects.select_for_update().filter(pk=community_id).first()
        detail_ids = list(
            RecordingReviewItem.objects.filter(event_detail__event_id=event_id)
            .order_by("event_detail_id")
            .values_list("event_detail_id", flat=True)
        )
        now = timezone.now()
        for detail_id in detail_ids:
            detail = (
                EventDetail.all_objects.select_for_update(of=("self",))
                .select_related("event__community")
                .filter(pk=detail_id)
                .first()
            )
            if detail is None or is_recording_consent_valid(detail):
                continue
            item = RecordingReviewItem.objects.select_for_update().filter(event_detail=detail).first()
            if item is None:
                continue
            if _compensate_revoked_item(
                detail=detail,
                item=item,
                now=now,
                target_detail_for_order=detail,
            ):
                queued_count += 1
    return queued_count
