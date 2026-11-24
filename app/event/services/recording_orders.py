"""EDL v1 確定指示キュー（RecordingEditOrder）の作成サービス。"""
from __future__ import annotations

import copy
import logging
from datetime import datetime
from typing import Any, NoReturn

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
    enqueue_compensating_stop_publish,
    is_order_lease_expired,
    is_recording_consent_valid,
    item_has_unstopped_video,
    lock_event_detail,
    lock_event_detail_or_none,
    mark_order_lease_expired,
    next_stop_target_video_id,
    serialize_item_state,
    validate_positive_revision,
)
from event.services.recording_storage import ensure_recording_consent

logger = logging.getLogger(__name__)


def _can_create_manual_stop_publish(item: RecordingReviewItem | None) -> bool:
    """手動 stop_publish を許可できる停止対象または既存動画があるかを返す。"""
    if item is None:
        return False
    return bool(item.youtube_draft_video_id) or item_has_unstopped_video(item)


def _ensure_order_consent(
    detail: EventDetail,
    item: RecordingReviewItem | None,
    *,
    order_type: str,
) -> None:
    if order_type == RecordingEditOrder.OrderType.APPLY_EDL:
        ensure_recording_consent(detail, action="create_order")
        return

    # stop_publish は、同意が public のままの場合に加えて、既に公開/下書き動画または
    # 停止待ち動画が存在し緊急停止が必要なケースのみ許可する（未同意の発表への不要な指示作成を遮断）。
    if not _can_create_manual_stop_publish(item):
        ensure_recording_consent(detail, action="stop_publish")


def _raise_order_conflict(
    detail_id_for_log: int | None,
    order_type: str,
    reason: str,
    message: str,
    item: RecordingReviewItem | None = None,
) -> NoReturn:
    """競合エラーを警告ログとともに送出する。"""
    logger.warning(
        "recording_order_conflict",
        extra={
            "event_type": "recording_order_conflict",
            "event_detail_id": detail_id_for_log,
            "order_type": order_type,
            "reason": reason,
        },
    )
    raise RecordingConflictError(
        message,
        code=reason,
        current_state=serialize_item_state(item) if item is not None else None,
    )


def _sweep_active_orders(
    *,
    item: RecordingReviewItem,
    now: datetime,
    detail_id_for_log: int | None,
) -> bool:
    """排他中の指示を検査し、リース期限切れを失効させた上で未完了が残るかを返す。"""
    active_orders = list(
        RecordingEditOrder.objects.select_for_update().filter(
            review_item=item,
            status__in=RecordingEditOrder.ACTIVE_STATUSES,
        )
    )
    still_active = False
    for active_order in active_orders:
        if is_order_lease_expired(active_order, now=now):
            mark_order_lease_expired(active_order, now=now, event_detail_id=detail_id_for_log)
        else:
            still_active = True
    return still_active


def _create_order_locked(
    *,
    detail: EventDetail | None,
    item: RecordingReviewItem,
    expected_revision: int,
    order_type: str,
    validated_incoming_edl: dict[str, Any] | None,
    requested_by: Any,
) -> RecordingEditOrder | RecordingConflictError:
    """ロック済み detail/item 上で改訂・排他を検査して確定指示または停止指示を作成する。"""
    detail_id_for_log = detail.pk if detail is not None else None
    now = timezone.now()
    swept = False
    if order_type == RecordingEditOrder.OrderType.APPLY_EDL and item.compensating_stop_pending:
        swept = True
        still_active = _sweep_active_orders(item=item, now=now, detail_id_for_log=detail_id_for_log)
        if not still_active and item_has_unstopped_video(item):
            enqueue_compensating_stop_publish(
                detail=detail,
                item=item,
                requested_by=requested_by,
            )
            return RecordingConflictError(
                "保留中の公開停止指示を優先投入したため確定指示を作成できません。",
                code="active_order_exists",
                current_state=serialize_item_state(item),
            )

    if order_type == RecordingEditOrder.OrderType.APPLY_EDL:
        if item.review_status == RecordingReviewItem.ReviewStatus.EXPIRED or (
            item.proxy_expires_at is not None and item.proxy_expires_at <= now
        ):
            _raise_order_conflict(
                detail_id_for_log,
                order_type,
                "proxy_expired",
                "プロキシ動画の保持期限が切れています。",
                item,
            )
        if item.is_public_locked:
            _raise_order_conflict(
                detail_id_for_log,
                order_type,
                "public_locked",
                "公開済みロック中のため apply_edl の確定指示は作成できません。",
                item,
            )

    if expected_revision != item.edl_revision:
        _raise_order_conflict(
            detail_id_for_log,
            order_type,
            "revision_conflict",
            "expected_revision が現在の EDL リビジョンと一致しません。",
            item,
        )
    if item.edl_revision >= MAX_REVISION:
        raise RecordingConflictError(
            "EDL リビジョン上限に達しています。",
            code="revision_limit_exceeded",
            current_state=serialize_item_state(item),
        )

    still_active = (
        _sweep_active_orders(item=item, now=now, detail_id_for_log=detail_id_for_log)
        if not swept
        else still_active
    )

    if still_active:
        _raise_order_conflict(
            detail_id_for_log,
            order_type,
            "active_order_exists",
            "未完了の確定指示が既に存在します。",
            item,
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
    if item.compensating_stop_pending and (
        order_type == RecordingEditOrder.OrderType.STOP_PUBLISH or not item_has_unstopped_video(item)
    ):
        item.compensating_stop_pending = False
        item_update_fields.append("compensating_stop_pending")
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
        try:
            _raise_order_conflict(
                detail_id_for_log,
                order_type,
                "active_order_exists",
                "未完了の確定指示が既に存在します。",
                item,
            )
        except RecordingConflictError as conflict_err:
            raise conflict_err from exc

    logger.info(
        "recording_order_created",
        extra={
            "event_type": "recording_order_created",
            "order_id": order.pk,
            "event_detail_id": detail_id_for_log,
            "order_type": order.order_type,
            "base_revision": order.base_revision,
            "target_revision": order.target_revision,
        },
    )
    return order


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

    conflict_exc: RecordingConflictError | None = None
    with transaction.atomic():
        is_stop_publish = order_type == RecordingEditOrder.OrderType.STOP_PUBLISH
        detail = lock_event_detail(detail_id, include_deleted=is_stop_publish)
        item = RecordingReviewItem.objects.select_for_update().filter(event_detail=detail).first()
        if detail.deleted_at is not None:
            if not _can_create_manual_stop_publish(item):
                raise RecordingNotFoundError("対象の発表が見つかりません。")
        _ensure_order_consent(detail, item, order_type=order_type)

        if item is None:
            _raise_order_conflict(
                detail.pk,
                order_type,
                "proxy_not_ready",
                "プロキシ動画がまだ登録されていないため確定指示を作成できません。",
            )

        result = _create_order_locked(
            detail=detail,
            item=item,
            expected_revision=expected_revision,
            order_type=order_type,
            validated_incoming_edl=validated_incoming_edl,
            requested_by=requested_by,
        )
        if isinstance(result, RecordingConflictError):
            conflict_exc = result

    if conflict_exc is not None:
        raise conflict_exc
    assert not isinstance(result, RecordingConflictError)
    return result


def create_stop_publish_order_for_review_item(
    review_item_id: int,
    *,
    expected_revision: int,
    requested_by: Any = None,
) -> RecordingEditOrder:
    """発表行の物理削除後も残存する RecordingReviewItem から公開停止指示を再試行する。"""
    validate_positive_revision(expected_revision, "expected_revision")
    ref = RecordingReviewItem.objects.values("event_detail_id").filter(pk=review_item_id).first()
    if ref is None:
        raise RecordingNotFoundError("対象の録画レビュー項目が見つかりません。")

    with transaction.atomic():
        detail = (
            lock_event_detail_or_none(ref["event_detail_id"], include_deleted=True)
            if ref["event_detail_id"] is not None
            else None
        )
        item = RecordingReviewItem.objects.select_for_update().filter(pk=review_item_id).first()
        if item is None:
            raise RecordingNotFoundError("対象の録画レビュー項目が見つかりません。")
        if item.event_detail_id is None:
            detail = None

        if detail is None or detail.deleted_at is not None:
            if not _can_create_manual_stop_publish(item):
                raise RecordingConflictError(
                    "停止対象の公開動画または公開状態が存在しません。",
                    code="nothing_to_stop",
                    current_state=serialize_item_state(item),
                )
        else:
            _ensure_order_consent(
                detail,
                item,
                order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
            )

        result = _create_order_locked(
            detail=detail,
            item=item,
            expected_revision=expected_revision,
            order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
            validated_incoming_edl=None,
            requested_by=requested_by,
        )
        assert not isinstance(result, RecordingConflictError)
        return result


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


def _compensate_revoked_detail_ids(detail_ids: list[int], now: datetime) -> int:
    queued_count = 0
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
        if item is not None and _compensate_revoked_item(
            detail=detail,
            item=item,
            now=now,
            target_detail_for_order=detail,
        ):
            queued_count += 1
    return queued_count


def revoke_recording_for_community(community_id: int) -> int:
    """集会の録画許可撤回に合わせて、配下発表の録画停止を一括予約する（Community -> EventDetail -> Item -> Order 順でロック）。"""
    with transaction.atomic():
        community = Community.objects.select_for_update().filter(pk=community_id).first()
        if community is None or community.recording_allowed:
            return 0
        detail_ids = list(
            RecordingReviewItem.objects.filter(event_detail__event__community_id=community_id)
            .order_by("event_detail_id")
            .values_list("event_detail_id", flat=True)
        )
        return _compensate_revoked_detail_ids(detail_ids, timezone.now())


def revoke_recording_for_event(event_id: int) -> int:
    """イベントの所属集会変更等で録画不許可となった配下発表の停止予約を行う（Community -> EventDetail -> Item -> Order 順）。"""
    community_id = Event.objects.filter(pk=event_id).values_list("community_id", flat=True).first()
    if community_id is None:
        return 0
    with transaction.atomic():
        Community.objects.select_for_update().filter(pk=community_id).first()
        detail_ids = list(
            RecordingReviewItem.objects.filter(event_detail__event_id=event_id)
            .order_by("event_detail_id")
            .values_list("event_detail_id", flat=True)
        )
        return _compensate_revoked_detail_ids(detail_ids, timezone.now())
