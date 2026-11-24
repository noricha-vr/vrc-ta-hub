"""スタッフ運営画面向けの録画レビュー読み取り専用クエリ・集約・コンテキスト構築（Layer 3）。"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.db.models import OuterRef, Prefetch, Q, QuerySet, Subquery
from django.utils import timezone

from community.constants import RECORDING_POLICY_DISPLAY_LABELS, RecordingPolicy
from event.models import EventDetail, RecordingEditOrder, RecordingReviewItem

from .edl import EDLValidationError, validate_edl_v1
from .errors import RecordingNotFoundError
from .storage import (
    MIN_PRESIGNED_EXPIRES_IN,
    RecordingConsentError,
    RecordingProxyExpiredError,
    RecordingProxyNotFoundError,
    RecordingStorageConfigError,
    RecordingStorageError,
    create_presigned_proxy_download_url,
)

logger = logging.getLogger(__name__)

STATUS_FILTER_CHOICES: tuple[tuple[str, str], ...] = (
    ("", "すべて"), ("needs_review", "要確認"), ("unrecorded", "未録画・未登録"),
    ("processing", "処理中"), ("unlisted_scheduled", "限定公開済み"),
    ("published", "公開済みロック"), ("private_fixed", "非公開確定"), ("expired", "期限切れ"),
)
ORDER_STATUS_FILTER_CHOICES: tuple[tuple[str, str], ...] = (
    ("", "すべて"), ("unconfirmed", "未確定"),
    (RecordingEditOrder.Status.QUEUED, "待機中 (queued)"), (RecordingEditOrder.Status.RUNNING, "処理中 (running)"),
    (RecordingEditOrder.Status.SUCCEEDED, "完了 (succeeded)"), (RecordingEditOrder.Status.FAILED, "失敗 (failed)"),
)
POLICY_FILTER_CHOICES: tuple[tuple[str, str], ...] = (
    ("", "すべて"), (RecordingPolicy.PUBLIC.value, "公開撮影 (public)"),
    (RecordingPolicy.ALLOWED.value, "旧設定 (allowed)"), (RecordingPolicy.FORBIDDEN.value, "撮影しない (forbidden)"),
)
_VALID_STATUS_FILTERS = frozenset(k for k, _ in STATUS_FILTER_CHOICES if k)
_VALID_ORDER_FILTERS = frozenset(k for k, _ in ORDER_STATUS_FILTER_CHOICES if k)
_VALID_POLICY_FILTERS = frozenset(k for k, _ in POLICY_FILTER_CHOICES if k)
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


@dataclass(frozen=True)
class RecordingRowSummary:
    detail: EventDetail
    review_item: RecordingReviewItem | None
    latest_order: RecordingEditOrder | None
    active_order: RecordingEditOrder | None
    title: str
    policy_badge: StatusBadge
    proxy_status: str
    proxy_badge: StatusBadge
    order_status: str
    order_badge: StatusBadge
    publish_badge: StatusBadge
    can_access_recording: bool
    is_public_locked: bool
    is_needs_review: bool
    can_trim_now: bool


@dataclass(frozen=True)
class RecordingListSummary:
    total_count: int
    needs_review_count: int
    unlisted_scheduled_count: int
    published_count: int
    failed_order_count: int
    needs_review_rows: list[RecordingRowSummary]


@dataclass(frozen=True)
class BoundaryCandidateOption:
    sec: float
    sec_display: str
    label: str


@dataclass(frozen=True)
class RecordingDetailContext:
    detail: EventDetail
    review_item: RecordingReviewItem | None
    title: str
    edl_revision: int
    edl_valid: bool
    safe_min_sec: float
    safe_max_sec: float
    trim_start_sec: float
    trim_end_sec: float
    start_candidates: list[BoundaryCandidateOption]
    end_candidates: list[BoundaryCandidateOption]
    cuts_count: int
    proxy_state: str
    proxy_url: str | None
    proxy_expires_at: datetime | None
    proxy_expires_in: int | None
    proxy_error_code: str
    proxy_error_message: str
    active_order: RecordingEditOrder | None
    latest_order: RecordingEditOrder | None
    recent_orders: list[RecordingEditOrder]
    is_public_locked: bool
    can_submit: bool
    policy_label: str
    publish_badge: StatusBadge
    order_badge: StatusBadge


def format_seconds_display(sec: float) -> str:
    """秒数を `M:SS` または `H:MM:SS`（小数部がある場合は `.d` 付き）に整形する。"""
    tenths = round(max(0.0, float(sec)) * 10)
    whole_sec, frac_tenth = divmod(tenths, 10)
    minutes, rem_sec = divmod(whole_sec, 60)
    hours, rem_min = divmod(minutes, 60)
    base = f"{hours}:{rem_min:02d}:{rem_sec:02d}" if hours else f"{rem_min}:{rem_sec:02d}"
    return f"{base}.{frac_tenth}" if frac_tenth else base


def needs_review_q() -> Q:
    """運営の確認・トリミング確定が必要な発表を選ぶ Q。"""
    return Q(
        recording_policy=RecordingPolicy.PUBLIC, event__community__recording_allowed=True,
        recording_review__review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
        recording_review__public_locked_at__isnull=True,
    ) & ~Q(recording_review__youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.PUBLIC)


def _expired_q(now: datetime) -> Q:
    return Q(recording_review__review_status=RecordingReviewItem.ReviewStatus.EXPIRED) | (
        ~Q(recording_review__proxy_r2_key="") & Q(recording_review__proxy_expires_at__isnull=False)
        & Q(recording_review__proxy_expires_at__lte=now)
    )


def _base_recording_queryset() -> QuerySet[EventDetail]:
    """一覧母集団: 承認済み発表（撮影許可集会の LT/SPECIAL、または既存録画アイテム付き）。"""
    return (
        EventDetail.objects.filter(status="approved")
        .filter(Q(event__community__recording_allowed=True, detail_type__in=("LT", "SPECIAL")) | Q(recording_review__isnull=False))
        .select_related("event", "event__community", "recording_review")
    )


def _apply_status_filter(qs: QuerySet[EventDetail], status_filter: str, now: datetime) -> QuerySet[EventDetail]:
    rs = RecordingReviewItem.ReviewStatus
    if status_filter == "needs_review":
        return qs.filter(needs_review_q())
    if status_filter == "unrecorded":
        return qs.filter(recording_policy=RecordingPolicy.PUBLIC, event__community__recording_allowed=True).filter(
            Q(recording_review__isnull=True) | Q(recording_review__review_status=rs.UNRECORDED)
            | (Q(recording_review__proxy_r2_key="") & ~Q(recording_review__review_status__in=(rs.PROCESSING, rs.PUBLISHED, rs.PRIVATE_FIXED, rs.EXPIRED)))
        )
    if status_filter == "published":
        return qs.filter(Q(recording_review__review_status=rs.PUBLISHED) | Q(recording_review__youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.PUBLIC))
    if status_filter == "unlisted_scheduled":
        return qs.filter(recording_review__review_status=rs.UNLISTED_SCHEDULED).exclude(_expired_q(now))
    if status_filter == "expired":
        return qs.filter(_expired_q(now))
    simple_status = {"processing": rs.PROCESSING, "private_fixed": rs.PRIVATE_FIXED}.get(status_filter)
    return qs.filter(recording_review__review_status=simple_status) if simple_status else qs


def build_recording_list_queryset(
    *, status_filter: str = "", order_filter: str = "", policy_filter: str = "", query: str = "", now: datetime | None = None,
) -> QuerySet[EventDetail]:
    """フィルタ・検索・最新オーダー注釈済みのスタッフ録画一覧 QuerySet を返す。"""
    current_time = now or timezone.now()
    qs = _base_recording_queryset()
    if status_filter in _VALID_STATUS_FILTERS:
        qs = _apply_status_filter(qs, status_filter, current_time)
    if policy_filter in _VALID_POLICY_FILTERS:
        qs = qs.filter(recording_policy=policy_filter)
    if trimmed := query.strip():
        qs = qs.filter(Q(theme__icontains=trimmed) | Q(h1__icontains=trimmed) | Q(speaker__icontains=trimmed) | Q(event__community__name__icontains=trimmed))

    latest_order_subquery = Subquery(
        RecordingEditOrder.objects.filter(review_item__event_detail_id=OuterRef("pk")).order_by("-created_at", "-pk").values("status")[:1]
    )
    annotated = qs.annotate(latest_order_status=latest_order_subquery)
    if order_filter in _VALID_ORDER_FILTERS:
        if order_filter == "unconfirmed":
            annotated = annotated.filter(Q(latest_order_status__isnull=True) | Q(latest_order_status=RecordingEditOrder.Status.CANCELED))
        else:
            annotated = annotated.filter(latest_order_status=order_filter)

    order_prefetch = Prefetch("recording_review__edit_orders", queryset=RecordingEditOrder.objects.order_by("-created_at", "-pk"), to_attr="prefetched_orders")
    return annotated.prefetch_related(order_prefetch).order_by("-event__date", "-start_time", "-pk")


def _resolve_proxy_state(detail: EventDetail, item: RecordingReviewItem | None, *, can_access: bool, now: datetime) -> tuple[str, StatusBadge]:
    if detail.recording_policy == RecordingPolicy.ALLOWED:
        return ("restricted", StatusBadge("非公開固定（旧設定）"))
    if detail.recording_policy == RecordingPolicy.FORBIDDEN:
        return ("forbidden", StatusBadge("非配信（再生不可）"))
    if not can_access:
        return ("consent_stopped", StatusBadge("同意停止（集会許可OFF）", "danger"))
    if item is None or not item.proxy_r2_key:
        if item is not None and item.review_status == RecordingReviewItem.ReviewStatus.PROCESSING:
            return ("processing", StatusBadge("処理中"))
        return ("unregistered", StatusBadge("未登録"))
    if item.review_status == RecordingReviewItem.ReviewStatus.EXPIRED:
        return ("expired", StatusBadge("期限切れ", "danger"))
    if item.proxy_expires_at is not None:
        remaining = (item.proxy_expires_at - now).total_seconds()
        if remaining <= 0:
            return ("expired", StatusBadge("期限切れ", "danger"))
        if remaining < MIN_PRESIGNED_EXPIRES_IN:
            return ("expired", StatusBadge("期限切れ（残15分未満）", "warning"))
    return ("available", StatusBadge("視聴可", "success"))


def _resolve_order_state(item: RecordingReviewItem | None) -> tuple[RecordingEditOrder | None, RecordingEditOrder | None, str, StatusBadge]:
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


def _resolve_publish_state(detail: EventDetail, item: RecordingReviewItem | None) -> tuple[StatusBadge, bool]:
    if detail.recording_policy == RecordingPolicy.ALLOWED:
        return (StatusBadge("非公開固定（旧設定）"), False)
    if detail.recording_policy == RecordingPolicy.FORBIDDEN:
        return (StatusBadge("非配信"), False)
    if item is not None and item.review_status == RecordingReviewItem.ReviewStatus.PRIVATE_FIXED:
        return (StatusBadge("非公開確定（公開停止・再公開不可）", "danger"), True) if item.is_public_locked else (StatusBadge("非公開確定"), False)
    if item is not None and item.is_public_locked:
        return (StatusBadge("公開済みロック", "success"), True)
    if item is not None and item.review_status == RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED:
        return (StatusBadge("公開前（限定公開済み）", "success"), False)
    return (StatusBadge("公開前"), False)


def _resolve_policy_badge(detail: EventDetail, *, community_allowed: bool) -> StatusBadge:
    if detail.recording_policy == RecordingPolicy.PUBLIC:
        return StatusBadge("公開撮影 (public)", "success") if community_allowed else StatusBadge("公開同意あり（集会側OFF）", "warning")
    if detail.recording_policy == RecordingPolicy.ALLOWED:
        return StatusBadge("旧設定 (allowed) ・非公開固定")
    return StatusBadge("撮影しない (forbidden)")


def build_recording_row_summary(detail: EventDetail, *, now: datetime | None = None) -> RecordingRowSummary:
    """一覧・要対応表示用の行サマリー DTO を組み立てる。"""
    current_time = now or timezone.now()
    item: RecordingReviewItem | None = getattr(detail, "recording_review", None)
    community_allowed = bool(detail.event.community.recording_allowed)
    can_access = detail.recording_policy == RecordingPolicy.PUBLIC and community_allowed
    proxy_status, proxy_badge = _resolve_proxy_state(detail, item, can_access=can_access, now=current_time)
    latest_order, active_order, order_status, order_badge = _resolve_order_state(item)
    publish_badge, is_public_locked = _resolve_publish_state(detail, item)
    is_needs_review = can_access and item is not None and item.review_status == RecordingReviewItem.ReviewStatus.NEEDS_REVIEW and not is_public_locked
    return RecordingRowSummary(
        detail=detail, review_item=item, latest_order=latest_order, active_order=active_order,
        title=detail.title or "(無題の発表)", policy_badge=_resolve_policy_badge(detail, community_allowed=community_allowed),
        proxy_status=proxy_status, proxy_badge=proxy_badge, order_status=order_status, order_badge=order_badge,
        publish_badge=publish_badge, can_access_recording=can_access, is_public_locked=is_public_locked,
        is_needs_review=is_needs_review, can_trim_now=can_access and proxy_status == "available" and not is_public_locked and active_order is None,
    )


def get_recording_list_summary(*, now: datetime | None = None) -> RecordingListSummary:
    """一覧上部の集計値と `.action-required` 用の要確認先頭件数を取得する。"""
    current_time = now or timezone.now()
    base = _base_recording_queryset()
    needs_review_qs = base.filter(needs_review_q()).order_by("-event__date", "-start_time", "-pk")
    latest_order_subquery = Subquery(
        RecordingEditOrder.objects.filter(review_item__event_detail_id=OuterRef("pk")).order_by("-created_at", "-pk").values("status")[:1]
    )
    failed_order_count = base.annotate(latest_order_status=latest_order_subquery).filter(latest_order_status=RecordingEditOrder.Status.FAILED).count()
    return RecordingListSummary(
        total_count=base.count(), needs_review_count=needs_review_qs.count(),
        unlisted_scheduled_count=base.filter(recording_review__review_status=RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED).exclude(_expired_q(current_time)).count(),
        published_count=base.filter(Q(recording_review__review_status=RecordingReviewItem.ReviewStatus.PUBLISHED) | Q(recording_review__youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.PUBLIC)).count(),
        failed_order_count=failed_order_count,
        needs_review_rows=[build_recording_row_summary(d, now=current_time) for d in needs_review_qs[:5]],
    )


def _extract_candidates(raw_list: Any, *, min_sec: float, max_sec: float) -> list[BoundaryCandidateOption]:
    if not isinstance(raw_list, list):
        return []
    options: list[BoundaryCandidateOption] = []
    for entry in raw_list:
        raw_sec = entry.get("sec", entry.get("time_sec")) if isinstance(entry, dict) else entry
        raw_label = entry.get("label") if isinstance(entry, dict) else ""
        if isinstance(raw_sec, bool) or not isinstance(raw_sec, (int, float)) or not math.isfinite(float(raw_sec)):
            continue
        sec = max(min_sec, min(max_sec, float(raw_sec)))
        options.append(BoundaryCandidateOption(sec=sec, sec_display=format_seconds_display(sec), label=raw_label.strip() if isinstance(raw_label, str) else ""))
    return options


def _parse_edl_for_context(
    item: RecordingReviewItem | None,
) -> tuple[bool, float, float, float, float, list[BoundaryCandidateOption], list[BoundaryCandidateOption], list[dict[str, Any]]]:
    fallback_max = float(item.proxy_duration_sec) if item and item.proxy_duration_sec and item.proxy_duration_sec > 0 else 60.0
    if item is None or not isinstance(item.edl_json, dict) or not item.edl_json:
        return (False, 0.0, fallback_max, 0.0, fallback_max, [], [], [])
    try:
        edl = validate_edl_v1(item.edl_json)
    except EDLValidationError:
        return (False, 0.0, fallback_max, 0.0, fallback_max, [], [], [])
    safe_min, safe_max = float(edl["safe_bounds"]["min_sec"]), float(edl["safe_bounds"]["max_sec"])
    cands = edl.get("boundary_candidates") or {}
    return (
        True, safe_min, safe_max, float(edl["trim"]["start_sec"]), float(edl["trim"]["end_sec"]),
        _extract_candidates(cands.get("start"), min_sec=safe_min, max_sec=safe_max),
        _extract_candidates(cands.get("end"), min_sec=safe_min, max_sec=safe_max), list(edl.get("cuts") or []),
    )


def _issue_proxy_url_safe(detail: EventDetail, item: RecordingReviewItem | None, *, issue_presigned_url: bool) -> tuple[str, str | None, datetime | None, int | None, str, str]:
    if not issue_presigned_url:
        if item is None or not item.proxy_r2_key:
            return ("missing", None, None, None, "proxy_not_found", "プロキシ動画がまだ登録されていません。")
        return ("ready", None, item.proxy_expires_at, None, "", "")
    try:
        result = create_presigned_proxy_download_url(detail)
        return ("ready", result.proxy_url, result.expires_at, result.expires_in, "", "")
    except RecordingProxyNotFoundError:
        return ("missing", None, None, None, "proxy_not_found", "プロキシ動画がまだ登録されていません。")
    except RecordingProxyExpiredError:
        err_reason, err_msg = ("proxy_expired", "プロキシ動画の保持期限が切れているか、残存時間が 15 分未満のため視聴できません。")
        state = "expired"
    except RecordingStorageConfigError:
        err_reason, err_msg = ("storage_config_error", "非公開プロキシストレージの設定が未構成または不正なため、動画 URL を発行できませんでした。")
        state = "error"
    except (RecordingStorageError, Exception):
        err_reason, err_msg = ("storage_error", "署名付き動画 URL の生成に失敗しました。時間を置いてページを再読み込みしてください。")
        state = "error"
    logger.warning("recording_review_proxy_url_error detail=%s reason=%s", detail.pk, err_reason, extra={"event_type": "recording_review_proxy_url_error", "event_detail_id": detail.pk, "reason": err_reason})
    return (state, None, None, None, err_reason, err_msg)


def build_recording_detail_context(detail_id: int, *, issue_presigned_url: bool = True, now: datetime | None = None) -> RecordingDetailContext:
    """スタッフ詳細・トリミング編集画面のコンテキストを厳格な同意検証つきで構築する。"""
    del now
    detail = EventDetail.objects.select_related("event", "event__community", "recording_review").filter(pk=detail_id, status="approved").first()
    if detail is None:
        raise RecordingNotFoundError("対象の発表が見つかりません。")
    if detail.recording_policy != RecordingPolicy.PUBLIC or not detail.event.community.recording_allowed:
        logger.warning("recording_proxy_url_denied", extra={"event_type": "recording_proxy_url_denied", "event_detail_id": detail.pk, "action": "detail_view", "reason": "consent_not_public"})
        raise RecordingConsentError("撮影公開の同意（recording_policy=public かつ集会の自動撮影許可）がないため表示できません。")

    item: RecordingReviewItem | None = getattr(detail, "recording_review", None)
    proxy_state, proxy_url, proxy_expires_at, proxy_expires_in, err_code, err_msg = _issue_proxy_url_safe(detail, item, issue_presigned_url=issue_presigned_url)
    edl_valid, safe_min, safe_max, trim_start, trim_end, start_cands, end_cands, cuts = _parse_edl_for_context(item)
    recent_orders = list(item.edit_orders.select_related("requested_by").order_by("-created_at", "-pk")[:5]) if item is not None else []
    active_order = next((o for o in recent_orders if o.status in RecordingEditOrder.ACTIVE_STATUSES), None)
    _, _, _, order_badge = _resolve_order_state(item)
    publish_badge, is_public_locked = _resolve_publish_state(detail, item)

    return RecordingDetailContext(
        detail=detail, review_item=item, title=detail.title or "(無題の発表)", edl_revision=item.edl_revision if item is not None else 1,
        edl_valid=edl_valid, safe_min_sec=safe_min, safe_max_sec=safe_max, trim_start_sec=trim_start, trim_end_sec=trim_end,
        start_candidates=start_cands, end_candidates=end_cands, cuts_count=len(cuts), proxy_state=proxy_state, proxy_url=proxy_url,
        proxy_expires_at=proxy_expires_at, proxy_expires_in=proxy_expires_in, proxy_error_code=err_code, proxy_error_message=err_msg,
        active_order=active_order, latest_order=recent_orders[0] if recent_orders else None, recent_orders=recent_orders,
        is_public_locked=is_public_locked, can_submit=bool(item is not None and edl_valid and proxy_state == "ready" and not is_public_locked and active_order is None),
        policy_label=RECORDING_POLICY_DISPLAY_LABELS.get(detail.recording_policy, detail.recording_policy),
        publish_badge=publish_badge, order_badge=order_badge,
    )


def estimate_remaining_minutes(expires_at: datetime | None, *, now: datetime | None = None) -> int | None:
    """残り分数（切り上げ、最小 1）を返す。"""
    if expires_at is None:
        return None
    sec = int((expires_at - (now or timezone.now())).total_seconds())
    return 0 if sec <= 0 else max(1, (sec + 59) // 60)
