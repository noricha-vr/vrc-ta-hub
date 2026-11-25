"""RecordingOpsSnapshot の JSON payload を欠損・型崩れ耐性付きで解析するヘルパー。"""
from __future__ import annotations

from dataclasses import dataclass
import posixpath
import re
from typing import Any
from urllib.parse import unquote, urlparse

from community.constants import RecordingPolicy

from .state_summary import StatusBadge

FUNNEL_PERIOD_KEYS: tuple[str, ...] = ("7", "30", "90")
DEFAULT_FUNNEL_PERIOD = "30"

FUNNEL_PERIOD_LABELS: dict[str, str] = {
    "7": "7日間",
    "30": "30日間",
    "90": "90日間",
}

_VENUE_STATUS_BADGES: dict[str, tuple[str, str]] = {
    "confirmed": ("会場確認済み", "success"),
    "ready": ("準備完了", "success"),
    "scouted": ("下見済み", "success"),
    "waiting": ("確認待ち", "warning"),
    "pending": ("確認中", "warning"),
    "scout": ("下見済み・許可待ち", "warning"),
    "none": ("未確認", ""),
    "private": ("非公開ワールド", ""),
    "noscreen": ("画面なし", "warning"),
    "dormant": ("休止中", ""),
    "unknown": ("不明", ""),
}


@dataclass(frozen=True)
class NextSessionSummary:
    day: str
    start_time: str
    community_id: int | None
    community_name: str
    event_url: str
    venue_status: str
    venue_status_label: str
    venue_status_tone: str
    automatic: bool | None
    talk_count: int | None


@dataclass(frozen=True)
class FunnelPeriodSummary:
    period_key: str
    label: str
    has_data: bool
    required: int
    recorded: int
    published: int
    articles: int
    articles_unknown: int
    publishable: int
    recorded_pct: int | None
    published_pct: int | None
    articles_pct: int | None


@dataclass(frozen=True)
class VenueCoverageSummary:
    has_data: bool
    total_communities: int
    scouted_communities: int
    allowed_communities: int
    scouted_and_allowed: int
    scouted_pct: int | None
    allowed_pct: int | None
    ready_pct: int | None
    group_ready: int
    group_scout: int
    group_waiting: int
    group_none: int
    none_active: int
    none_dormant: int
    gap_private: int
    gap_unknown: int
    gap_noscreen: int
    gap_dormant: int


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def as_str(value: Any, default: str = "") -> str:
    return value.strip() if isinstance(value, str) else default


def as_optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def as_optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and stripped.isascii() and stripped.isdigit():
            try:
                return int(stripped)
            except ValueError:
                return None
    return None


def as_non_negative_int(value: Any, default: int = 0) -> int:
    parsed = as_optional_int(value)
    if parsed is None or parsed < 0:
        return default
    return parsed


def pct(numerator: int, denominator: int) -> int | None:
    if denominator <= 0 or numerator < 0:
        return None
    if numerator == 0:
        return 0
    if numerator >= denominator:
        return 100
    return min(100, (numerator * 100 + denominator // 2) // denominator)


def safe_url(value: Any) -> str:
    raw = as_str(value)
    if not raw:
        return ""
    if raw.startswith("/") and not raw.startswith("//"):
        return raw
    try:
        parsed = urlparse(raw)
    except ValueError:
        return ""
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return raw
    return ""


_RECORDING_DETAIL_PATH_RE = re.compile(r"/manage/recordings/(\d+)(?:/|$)")


def extract_recording_detail_id_from_url(url: str) -> int | None:
    """URL が録画詳細画面を指す場合、正規化後の detail_id を返す。"""
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    raw_path = unquote(parsed.path or url)
    normalized = posixpath.normpath(raw_path)
    match = _RECORDING_DETAIL_PATH_RE.search(normalized)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def resolve_venue_status(raw_status: Any) -> tuple[str, str, str]:
    status = as_str(raw_status).lower()
    if not status:
        return ("", "未設定", "")
    label, tone = _VENUE_STATUS_BADGES.get(status, (status, ""))
    return (status, label, tone)


def snapshot_policy_badge(raw_policy: str) -> tuple[StatusBadge, bool]:
    if raw_policy == RecordingPolicy.ALLOWED.value:
        return (StatusBadge("非公開固定（旧設定）"), True)
    if raw_policy == RecordingPolicy.FORBIDDEN.value:
        return (StatusBadge("非配信"), True)
    if raw_policy == RecordingPolicy.PUBLIC.value:
        return (StatusBadge("公開撮影 (public)", "success"), False)
    return (StatusBadge("設定未確認"), False)


def parse_next_session(raw_session: Any) -> NextSessionSummary | None:
    data = as_dict(raw_session)
    if not data:
        return None
    status_code, status_label, status_tone = resolve_venue_status(data.get("venue_status"))
    return NextSessionSummary(
        day=as_str(data.get("day"), "日付未設定"),
        start_time=as_str(data.get("start_time")),
        community_id=as_optional_int(data.get("community_id")),
        community_name=as_str(data.get("community_name"), "集会名未設定"),
        event_url=safe_url(data.get("event_url")),
        venue_status=status_code,
        venue_status_label=status_label,
        venue_status_tone=status_tone,
        automatic=as_optional_bool(data.get("automatic")),
        talk_count=as_optional_int(data.get("talk_count")),
    )


def parse_funnel_period(raw_funnel: Any, period_key: str) -> FunnelPeriodSummary:
    periods = as_dict(as_dict(raw_funnel).get("periods"))
    data = as_dict(periods.get(period_key))
    required = as_non_negative_int(data.get("required"))
    recorded = as_non_negative_int(data.get("recorded"))
    published = as_non_negative_int(data.get("published"))
    articles = as_non_negative_int(data.get("articles"))
    articles_unknown = as_non_negative_int(data.get("articles_unknown"))
    has_publishable = "publishable" in data and as_optional_int(data.get("publishable")) is not None
    publishable = as_non_negative_int(data.get("publishable"), default=required) if has_publishable else required
    publish_denom = publishable if has_publishable else required
    return FunnelPeriodSummary(
        period_key=period_key,
        label=FUNNEL_PERIOD_LABELS.get(period_key, f"{period_key}日間"),
        has_data=bool(data),
        required=required,
        recorded=recorded,
        published=published,
        articles=articles,
        articles_unknown=articles_unknown,
        publishable=publishable,
        recorded_pct=pct(recorded, required),
        published_pct=pct(published, publish_denom),
        articles_pct=pct(articles, required),
    )


def parse_venue_coverage(raw_coverage: Any) -> VenueCoverageSummary:
    data = as_dict(raw_coverage)
    groups = as_dict(data.get("groups"))
    gap = as_dict(data.get("gap_breakdown"))
    total = as_non_negative_int(data.get("total_communities"))
    scouted = as_non_negative_int(data.get("scouted_communities"))
    allowed = as_non_negative_int(data.get("allowed_communities"))
    ready = as_non_negative_int(data.get("scouted_and_allowed"))
    return VenueCoverageSummary(
        has_data=bool(data),
        total_communities=total,
        scouted_communities=scouted,
        allowed_communities=allowed,
        scouted_and_allowed=ready,
        scouted_pct=pct(scouted, total),
        allowed_pct=pct(allowed, total),
        ready_pct=pct(ready, total),
        group_ready=as_non_negative_int(groups.get("ready"), default=ready),
        group_scout=as_non_negative_int(groups.get("scout")),
        group_waiting=as_non_negative_int(groups.get("waiting")),
        group_none=as_non_negative_int(groups.get("none")),
        none_active=as_non_negative_int(data.get("none_active")),
        none_dormant=as_non_negative_int(data.get("none_dormant")),
        gap_private=as_non_negative_int(gap.get("private")),
        gap_unknown=as_non_negative_int(gap.get("unknown")),
        gap_noscreen=as_non_negative_int(gap.get("noscreen")),
        gap_dormant=as_non_negative_int(gap.get("dormant")),
    )
