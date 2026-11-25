"""録画運用ダッシュボードの読み取り専用コンテキスト構築（Layer 3）。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.urls import reverse
from django.utils import timezone

from community.constants import RecordingPolicy
from community.models import CommunityMember
from event.models import EventDetail, RecordingOpsSnapshot

from .article_status import ArticleStatusSummary, resolve_article_status
from .snapshot_parser import (
    DEFAULT_FUNNEL_PERIOD,
    FUNNEL_PERIOD_KEYS,
    FUNNEL_PERIOD_LABELS,
    FunnelPeriodSummary,
    NextSessionSummary,
    VenueCoverageSummary,
    as_dict,
    as_list,
    as_non_negative_int,
    as_optional_bool,
    as_optional_int,
    as_str,
    extract_recording_detail_id_from_url,
    parse_funnel_period,
    parse_next_session,
    parse_venue_coverage,
    resolve_venue_status,
    safe_url,
    snapshot_policy_badge,
)
from .state_summary import StatusBadge, resolve_policy_badge

SNAPSHOT_STALE_THRESHOLD = timedelta(hours=24)

__all__ = [
    "ArticleStatusSummary",
    "DEFAULT_FUNNEL_PERIOD",
    "DashboardActionItem",
    "FUNNEL_PERIOD_KEYS",
    "LinkedDetailContext",
    "RecordingOpsDashboardContext",
    "SNAPSHOT_STALE_THRESHOLD",
    "ScheduleSessionSummary",
    "ScheduleTalkSummary",
    "build_recording_ops_dashboard_context",
    "resolve_article_status",
]


@dataclass(frozen=True)
class LinkedDetailContext:
    detail_id: int
    article_status: ArticleStatusSummary
    recording_policy: str
    policy_badge: StatusBadge
    can_access_recording: bool
    recording_detail_url: str | None = None


@dataclass(frozen=True)
class DashboardActionItem:
    day: str
    community_id: int | None
    community_name: str
    detail_id: int | None
    title: str
    label: str
    reason: str
    action_url: str
    linked_detail: LinkedDetailContext | None


@dataclass(frozen=True)
class ScheduleTalkSummary:
    detail_id: int | None
    speaker: str
    theme: str
    recording_policy: str
    policy_badge: StatusBadge
    linked_detail: LinkedDetailContext | None
    is_restricted_policy: bool


@dataclass(frozen=True)
class ScheduleSessionSummary:
    day: str
    start_time: str
    community_id: int | None
    community_name: str
    automatic: bool | None
    venue_status: str
    venue_status_label: str
    venue_status_tone: str
    talk_count: int
    talks: list[ScheduleTalkSummary]


@dataclass(frozen=True)
class RecordingOpsDashboardContext:
    has_snapshot: bool
    snapshot_id: int | None
    schema_version: int | None
    snapshot_at: datetime | None
    received_at: datetime | None
    is_stale: bool
    stale_hours: int | None
    elapsed_display: str
    selected_period: str
    period_tabs: list[dict[str, Any]]
    funnel_since: str
    active_funnel: FunnelPeriodSummary
    next_session: NextSessionSummary | None
    action_items: list[DashboardActionItem]
    venue_coverage: VenueCoverageSummary
    upcoming_schedule: list[ScheduleSessionSummary]


def _format_elapsed(snapshot_at: datetime, now: datetime) -> tuple[str, bool, int | None]:
    total_sec = max(0, int((now - snapshot_at).total_seconds()))
    is_stale = (now - snapshot_at) >= SNAPSHOT_STALE_THRESHOLD
    hours = total_sec // 3600
    if total_sec < 60:
        return ("1分未満前", is_stale, hours)
    if total_sec < 3600:
        return (f"約{total_sec // 60}分前", is_stale, hours)
    if hours < 48:
        return (f"約{hours}時間前", is_stale, hours)
    days = hours // 24
    return (f"約{days}日前", is_stale, hours)


def _collect_detail_ids(payload: dict[str, Any]) -> set[int]:
    ids: set[int] = set()
    for raw_item in as_list(payload.get("action_items")):
        item_dict = as_dict(raw_item)
        detail_id = as_optional_int(item_dict.get("detail_id"))
        if detail_id is not None and detail_id > 0:
            ids.add(detail_id)
        target_id = extract_recording_detail_id_from_url(safe_url(item_dict.get("action_url")))
        if target_id is not None and target_id > 0:
            ids.add(target_id)
    for raw_session in as_list(payload.get("upcoming_schedule")):
        for raw_talk in as_list(as_dict(raw_session).get("talks")):
            detail_id = as_optional_int(as_dict(raw_talk).get("detail_id"))
            if detail_id is not None and detail_id > 0:
                ids.add(detail_id)
    return ids


def _build_linked_details(detail_ids: set[int], *, user: Any) -> dict[int, LinkedDetailContext]:
    if not detail_ids:
        return {}
    managed_ids: set[int] | None = None
    if user is not None and getattr(user, "is_authenticated", False) and not getattr(user, "is_superuser", False):
        managed_ids = set(
            CommunityMember.objects.filter(user=user).values_list("community_id", flat=True)
        )
    details = EventDetail.objects.filter(pk__in=detail_ids).select_related("event", "event__community")
    linked: dict[int, LinkedDetailContext] = {}
    for detail in details:
        community_allowed = bool(detail.event.community.recording_allowed)
        can_access = (
            detail.status == "approved"
            and detail.recording_policy == RecordingPolicy.PUBLIC
            and community_allowed
        )
        linked[detail.pk] = LinkedDetailContext(
            detail_id=detail.pk,
            article_status=resolve_article_status(detail, user=user, managed_community_ids=managed_ids),
            recording_policy=detail.recording_policy,
            policy_badge=resolve_policy_badge(detail, community_allowed=community_allowed),
            can_access_recording=can_access,
            recording_detail_url=(
                reverse("event:recording_review_detail", kwargs={"detail_id": detail.pk})
                if can_access
                else None
            ),
        )
    return linked


def _parse_action_items(
    raw_items: Any,
    *,
    linked_details: dict[int, LinkedDetailContext],
) -> list[DashboardActionItem]:
    items: list[DashboardActionItem] = []
    for entry in as_list(raw_items):
        data = as_dict(entry)
        if not data:
            continue
        detail_id = as_optional_int(data.get("detail_id"))
        linked = linked_details.get(detail_id) if detail_id is not None else None
        action_url = safe_url(data.get("action_url"))
        target_detail_id = extract_recording_detail_id_from_url(action_url)
        if target_detail_id is not None:
            target_linked = linked_details.get(target_detail_id)
            if target_linked is None or not target_linked.can_access_recording:
                action_url = ""
        items.append(
            DashboardActionItem(
                day=as_str(data.get("day")),
                community_id=as_optional_int(data.get("community_id")),
                community_name=as_str(data.get("community_name"), "集会名未設定"),
                detail_id=detail_id,
                title=as_str(data.get("title"), "(無題の発表)"),
                label=as_str(data.get("label"), "確認が必要です"),
                reason=as_str(data.get("reason")),
                action_url=action_url,
                linked_detail=linked,
            )
        )
    return items


def _parse_upcoming_schedule(
    raw_schedule: Any,
    *,
    linked_details: dict[int, LinkedDetailContext],
) -> list[ScheduleSessionSummary]:
    sessions: list[ScheduleSessionSummary] = []
    for entry in as_list(raw_schedule):
        data = as_dict(entry)
        if not data:
            continue
        status_code, status_label, status_tone = resolve_venue_status(data.get("venue_status"))
        talks: list[ScheduleTalkSummary] = []
        for raw_talk in as_list(data.get("talks")):
            talk_data = as_dict(raw_talk)
            if not talk_data:
                continue
            detail_id = as_optional_int(talk_data.get("detail_id"))
            linked = linked_details.get(detail_id) if detail_id is not None else None
            if linked is not None:
                policy = linked.recording_policy
                policy_badge = linked.policy_badge
                is_restricted = not linked.can_access_recording
            else:
                policy = as_str(talk_data.get("recording_policy"))
                policy_badge, is_restricted = snapshot_policy_badge(policy)
            talks.append(
                ScheduleTalkSummary(
                    detail_id=detail_id,
                    speaker=as_str(talk_data.get("speaker"), "発表者未設定"),
                    theme=as_str(talk_data.get("theme"), "(無題の発表)"),
                    recording_policy=policy,
                    policy_badge=policy_badge,
                    linked_detail=linked,
                    is_restricted_policy=is_restricted,
                )
            )
        talk_count = as_non_negative_int(data.get("talk_count"), default=len(talks))
        sessions.append(
            ScheduleSessionSummary(
                day=as_str(data.get("day"), "日付未設定"),
                start_time=as_str(data.get("start_time")),
                community_id=as_optional_int(data.get("community_id")),
                community_name=as_str(data.get("community_name"), "集会名未設定"),
                automatic=as_optional_bool(data.get("automatic")),
                venue_status=status_code,
                venue_status_label=status_label,
                venue_status_tone=status_tone,
                talk_count=talk_count,
                talks=talks,
            )
        )
    return sessions


def build_recording_ops_dashboard_context(
    *,
    user: Any = None,
    period: str = DEFAULT_FUNNEL_PERIOD,
    now: datetime | None = None,
) -> RecordingOpsDashboardContext:
    """最新の RecordingOpsSnapshot と Hub 側 EventDetail を統合したダッシュボード DTO を返す。"""
    selected_period = period if period in FUNNEL_PERIOD_KEYS else DEFAULT_FUNNEL_PERIOD
    period_tabs = [
        {"key": key, "label": FUNNEL_PERIOD_LABELS[key], "active": key == selected_period}
        for key in FUNNEL_PERIOD_KEYS
    ]
    snapshot = RecordingOpsSnapshot.objects.order_by("-snapshot_at", "-id").first()
    if snapshot is None:
        return RecordingOpsDashboardContext(
            has_snapshot=False,
            snapshot_id=None,
            schema_version=None,
            snapshot_at=None,
            received_at=None,
            is_stale=False,
            stale_hours=None,
            elapsed_display="",
            selected_period=selected_period,
            period_tabs=period_tabs,
            funnel_since="",
            active_funnel=parse_funnel_period({}, selected_period),
            next_session=None,
            action_items=[],
            venue_coverage=parse_venue_coverage({}),
            upcoming_schedule=[],
        )

    current_time = now or timezone.now()
    elapsed_display, is_stale, stale_hours = _format_elapsed(snapshot.snapshot_at, current_time)
    payload = as_dict(snapshot.payload)
    detail_ids = _collect_detail_ids(payload)
    linked_details = _build_linked_details(detail_ids, user=user)

    return RecordingOpsDashboardContext(
        has_snapshot=True,
        snapshot_id=snapshot.pk,
        schema_version=snapshot.schema_version,
        snapshot_at=snapshot.snapshot_at,
        received_at=snapshot.received_at,
        is_stale=is_stale,
        stale_hours=stale_hours,
        elapsed_display=elapsed_display,
        selected_period=selected_period,
        period_tabs=period_tabs,
        funnel_since=as_str(as_dict(payload.get("funnel_stats")).get("since")),
        active_funnel=parse_funnel_period(payload.get("funnel_stats"), selected_period),
        next_session=parse_next_session(payload.get("next_session")),
        action_items=_parse_action_items(payload.get("action_items"), linked_details=linked_details),
        venue_coverage=parse_venue_coverage(payload.get("venue_coverage")),
        upcoming_schedule=_parse_upcoming_schedule(payload.get("upcoming_schedule"), linked_details=linked_details),
    )
