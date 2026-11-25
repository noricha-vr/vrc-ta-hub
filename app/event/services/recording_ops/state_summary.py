"""録画レビュー画面向けの共通ステータス判定・バッジ・経過時間ヘルパー（Layer 3）。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from community.constants import RecordingPolicy
from event.models import EventDetail, RecordingEditOrder, RecordingReviewItem

from .errors import STALLED_QUEUED_ORDER_THRESHOLD, STALLED_RUNNING_ORDER_THRESHOLD
from .stop_queue import item_has_unstopped_video
from .storage import MIN_PRESIGNED_EXPIRES_IN

_ORDER_BADGES: dict[str, tuple[str, str]] = {
    RecordingEditOrder.Status.QUEUED: ("待機中 (queued)", "warning"),
    RecordingEditOrder.Status.RUNNING: ("処理中 (running)", "warning"),
    RecordingEditOrder.Status.SUCCEEDED: ("完了 (succeeded)", "success"),
    RecordingEditOrder.Status.FAILED: ("失敗 (failed)", "danger"),
    RecordingEditOrder.Status.CANCELED: ("未確定（取消済み）", ""),
}


@dataclass(frozen=True)
class StatusBadge:
    label: str
    tone: str = ""


def format_seconds_display(sec: float) -> str:
    """秒数を `M:SS` または `H:MM:SS`（小数部がある場合は `.d` 付き）に整形する。"""
    tenths = round(max(0.0, float(sec)) * 10)
    whole_sec, frac_tenth = divmod(tenths, 10)
    minutes, rem_sec = divmod(whole_sec, 60)
    hours, rem_min = divmod(minutes, 60)
    base = f"{hours}:{rem_min:02d}:{rem_sec:02d}" if hours else f"{rem_min}:{rem_sec:02d}"
    return f"{base}.{frac_tenth}" if frac_tenth else base


def is_deleted_after_upload(item: RecordingReviewItem) -> bool:
    """R2 ライフサイクル等によるアップロード後プロキシ削除状態かを判定する。"""
    return (
        not item.proxy_r2_key
        and item.proxy_uploaded_at is not None
        and item.review_status
        not in (RecordingReviewItem.ReviewStatus.UNRECORDED, RecordingReviewItem.ReviewStatus.PROCESSING)
    )


def is_item_proxy_expired(item: RecordingReviewItem | None, *, now: datetime) -> bool:
    """プロキシ動画が期限切れまたは削除済みかを判定する。"""
    if item is None:
        return False
    return (
        item.review_status == RecordingReviewItem.ReviewStatus.EXPIRED
        or (item.proxy_expires_at is not None and item.proxy_expires_at <= now)
        or is_deleted_after_upload(item)
    )


def is_order_stalled(order: RecordingEditOrder | None, *, now: datetime) -> bool:
    """queued または running のオーダーが閾値以上滞留しているか判定する。"""
    if order is None:
        return False
    if order.status == RecordingEditOrder.Status.QUEUED:
        return order.created_at <= now - STALLED_QUEUED_ORDER_THRESHOLD
    if order.status == RecordingEditOrder.Status.RUNNING:
        ref = order.started_at or order.created_at
        return ref <= now - STALLED_RUNNING_ORDER_THRESHOLD
    return False


def estimate_stalled_minutes(order: RecordingEditOrder | None, *, now: datetime) -> int | None:
    """滞留オーダーの経過分数を返す（滞留中でなければ None）。"""
    if not is_order_stalled(order, now=now) or order is None:
        return None
    ref = (
        (order.started_at or order.created_at)
        if order.status == RecordingEditOrder.Status.RUNNING
        else order.created_at
    )
    return max(1, int((now - ref).total_seconds() // 60))


def resolve_proxy_state(
    detail: EventDetail,
    item: RecordingReviewItem | None,
    *,
    can_access: bool,
    now: datetime,
) -> tuple[str, StatusBadge]:
    """一覧・サマリー向けのプロキシ表示状態とバッジを返す。"""
    if detail.recording_policy == RecordingPolicy.ALLOWED:
        return ("restricted", StatusBadge("非公開固定（旧設定）"))
    if detail.recording_policy == RecordingPolicy.FORBIDDEN:
        return ("forbidden", StatusBadge("非配信（再生不可）"))
    if not can_access:
        if detail.status != "approved":
            return ("unapproved", StatusBadge("未承認（再生不可）", "danger"))
        return ("consent_stopped", StatusBadge("同意停止（集会許可OFF）", "danger"))
    if is_item_proxy_expired(item, now=now):
        return ("expired", StatusBadge("期限切れ", "danger"))
    if item is None or not item.proxy_r2_key:
        if item is not None and item.review_status == RecordingReviewItem.ReviewStatus.PROCESSING:
            return ("processing", StatusBadge("処理中"))
        return ("unregistered", StatusBadge("未登録"))
    if item.proxy_expires_at is not None and (item.proxy_expires_at - now).total_seconds() < MIN_PRESIGNED_EXPIRES_IN:
        return ("expired", StatusBadge("期限切れ（残15分未満）", "warning"))
    return ("available", StatusBadge("視聴可", "success"))


def resolve_order_state(
    item: RecordingReviewItem | None,
) -> tuple[RecordingEditOrder | None, RecordingEditOrder | None, str, StatusBadge]:
    """対象アイテムの最新オーダー・進行中オーダー・表示状態バッジを返す。"""
    if item is None:
        return (None, None, "unconfirmed", StatusBadge("未確定"))
    prefetched = getattr(item, "prefetched_orders", None)
    orders = list(prefetched) if prefetched is not None else list(item.edit_orders.order_by("-created_at", "-pk")[:5])
    latest = orders[0] if orders else None
    active = next((o for o in orders if o.status in RecordingEditOrder.ACTIVE_STATUSES), None)
    if active is not None:
        label, tone = _ORDER_BADGES.get(active.status, (active.status, "warning"))
        return (latest, active, active.status, StatusBadge(label, tone))
    if latest is None or latest.status == RecordingEditOrder.Status.CANCELED:
        return (latest, None, "unconfirmed", StatusBadge("未確定"))
    label, tone = _ORDER_BADGES.get(latest.status, (latest.status, ""))
    return (latest, None, latest.status, StatusBadge(label, tone))


def resolve_publish_state(detail: EventDetail, item: RecordingReviewItem | None) -> tuple[StatusBadge, bool]:
    """公開状態バッジおよび公開済みロック有無を返す。"""
    if detail.recording_policy == RecordingPolicy.ALLOWED:
        return (StatusBadge("非公開固定（旧設定）"), False)
    if detail.recording_policy == RecordingPolicy.FORBIDDEN:
        return (StatusBadge("非配信"), False)
    if item is not None and item.review_status == RecordingReviewItem.ReviewStatus.PRIVATE_FIXED:
        return (
            (StatusBadge("非公開確定（公開停止・再公開不可）", "danger"), True)
            if item.is_public_locked
            else (StatusBadge("非公開確定"), False)
        )
    if item is not None and item.is_public_locked:
        return (StatusBadge("公開済みロック", "success"), True)
    if item is not None and item.review_status == RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED:
        return (StatusBadge("公開前（限定公開済み）", "success"), False)
    return (StatusBadge("公開前"), False)


def resolve_policy_badge(detail: EventDetail, *, community_allowed: bool) -> StatusBadge:
    """撮影ポリシーの表示バッジを返す。"""
    if detail.recording_policy == RecordingPolicy.PUBLIC:
        return (
            StatusBadge("公開撮影 (public)", "success")
            if community_allowed
            else StatusBadge("公開同意あり（集会側OFF）", "warning")
        )
    if detail.recording_policy == RecordingPolicy.ALLOWED:
        return StatusBadge("旧設定 (allowed) ・非公開固定")
    return StatusBadge("撮影しない (forbidden)")


def compute_order_action_flags(
    item: RecordingReviewItem | None,
    latest_order: RecordingEditOrder | None,
    active_order: RecordingEditOrder | None,
    *,
    can_access: bool,
    is_expired: bool,
    is_public_locked: bool,
) -> tuple[bool, RecordingEditOrder | None, bool, RecordingEditOrder | None, bool]:
    """失敗オーダーの再試行可否および queued/failed オーダーの取り消し可否を算出する。"""
    failed_latest = (
        latest_order
        if (active_order is None and latest_order is not None and latest_order.status == RecordingEditOrder.Status.FAILED)
        else None
    )
    can_retry = False
    if failed_latest is not None and item is not None:
        if failed_latest.order_type == RecordingEditOrder.OrderType.STOP_PUBLISH:
            can_retry = item_has_unstopped_video(item)
        else:
            can_retry = can_access and not is_expired and not is_public_locked and bool(item.proxy_r2_key)

    cancelable: RecordingEditOrder | None = None
    if active_order is not None and active_order.status == RecordingEditOrder.Status.QUEUED:
        cancelable = active_order
    elif failed_latest is not None:
        cancelable = failed_latest

    can_cancel = False
    if cancelable is not None and item is not None:
        if cancelable.order_type == RecordingEditOrder.OrderType.STOP_PUBLISH:
            can_cancel = can_access and not item.compensating_stop_pending
        else:
            can_cancel = can_access
    if not can_cancel:
        cancelable = None
    return (failed_latest is not None, failed_latest, can_retry, cancelable, can_cancel)
