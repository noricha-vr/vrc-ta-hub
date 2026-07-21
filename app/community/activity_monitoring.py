"""集会の活動停止候補抽出と、安全なソフトアーカイブを扱う。"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Max, Min, Q
from django.utils import timezone

from community.models import Community

logger = logging.getLogger(__name__)

DEFAULT_INACTIVE_DAYS = 90
DEFAULT_REQUIRED_INACTIVE_CHECKS = 2
DEFAULT_MIN_INACTIVE_CONFIDENCE = 0.75
DEFAULT_EXPLICIT_END_CONFIDENCE = 0.90
MAX_CANDIDATES = 200

STATUS_EXPLICITLY_ENDED = "explicitly_ended"
STATUS_NO_RECENT_EVIDENCE = "no_recent_evidence"
ARCHIVABLE_STATUSES = {STATUS_EXPLICITLY_ENDED, STATUS_NO_RECENT_EVIDENCE}

_X_HANDLE_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_X_RESERVED_PATHS = {
    "compose",
    "explore",
    "hashtag",
    "home",
    "i",
    "intent",
    "messages",
    "notifications",
    "search",
    "share",
}


@dataclass(frozen=True)
class ActivityCandidate:
    community_id: int
    name: str
    sns_url: str
    twitter_hashtags: list[str]
    organizers: str
    frequency: str
    group_url: str
    metadata_updated_at: date | None
    last_scheduled_event_date: date | None
    next_scheduled_event_date: date | None
    report_count: int
    official_x_handle: str

    def as_dict(self) -> dict[str, object]:
        return {
            "communityId": self.community_id,
            "name": self.name,
            "snsUrl": self.sns_url,
            "twitterHashtags": self.twitter_hashtags,
            "organizers": self.organizers,
            "frequency": self.frequency,
            "groupUrl": self.group_url,
            "metadataUpdatedAt": _date_string(self.metadata_updated_at),
            "lastScheduledEventDate": _date_string(self.last_scheduled_event_date),
            "nextScheduledEventDate": _date_string(self.next_scheduled_event_date),
            "reportCount": self.report_count,
            "officialXHandle": self.official_x_handle,
            "hasReliableXIdentifier": bool(
                self.official_x_handle or self.twitter_hashtags
            ),
        }


@dataclass(frozen=True)
class ArchiveResult:
    community_id: int
    community_name: str
    action: str
    archived: bool
    end_at: date | None

    def as_dict(self) -> dict[str, object]:
        return {
            "communityId": self.community_id,
            "communityName": self.community_name,
            "action": self.action,
            "archived": self.archived,
            "endAt": _date_string(self.end_at),
        }


def get_activity_candidates(
    *,
    inactive_days: int | None = None,
    limit: int = MAX_CANDIDATES,
) -> list[ActivityCandidate]:
    """メタデータ更新が止まっている公開集会をX検索候補として返す。

    ``Event`` は定期ルールから先の日付まで自動生成されるため、開催実績とは
    みなさない。直近・次回予定日はGrokの同定補助として返すだけにする。
    """
    configured_inactive_days = _setting_int(
        "COMMUNITY_ACTIVITY_INACTIVE_DAYS",
        DEFAULT_INACTIVE_DAYS,
    )
    inactive_days = max(
        configured_inactive_days,
        inactive_days or configured_inactive_days,
    )
    limit = min(max(int(limit), 1), MAX_CANDIDATES)
    today = timezone.localdate()
    cutoff = today - timedelta(days=inactive_days)

    communities = (
        Community.objects.filter(
            status="approved",
            end_at__isnull=True,
        )
        .annotate(
            last_scheduled_event_date=Max(
                "events__date",
                filter=Q(events__date__lte=today),
            ),
            next_scheduled_event_date=Min(
                "events__date",
                filter=Q(events__date__gte=today),
            ),
            report_count=Count("reports", distinct=True),
        )
        .order_by("-report_count", "id")
    )

    candidates: list[ActivityCandidate] = []
    for community in communities.iterator():
        if _is_partner_only(community.tags):
            continue
        if community.updated_at is not None and community.updated_at > cutoff:
            continue

        candidates.append(
            ActivityCandidate(
                community_id=community.pk,
                name=community.name,
                sns_url=community.sns_url or "",
                twitter_hashtags=community.hashtag_names,
                organizers=community.organizers or "",
                frequency=community.frequency or "",
                group_url=community.group_url or "",
                metadata_updated_at=community.updated_at,
                last_scheduled_event_date=community.last_scheduled_event_date,
                next_scheduled_event_date=community.next_scheduled_event_date,
                report_count=community.report_count,
                official_x_handle=extract_x_handle(community.sns_url),
            )
        )
        if len(candidates) >= limit:
            break

    return candidates


def archive_inactive_community(
    *,
    community_id: int,
    status: str,
    confidence: float,
    evidence_urls: list[str],
    consecutive_inactive_checks: int,
    inactive_days: int | None = None,
) -> ArchiveResult:
    """ローカル判定を再検証し、条件を満たす集会だけソフトアーカイブする。

    自動処理では将来イベントや定期ルールを削除しない。誤判定時に管理画面の
    「再開」で戻せるよう、``end_at`` の設定だけを行う。
    """
    if status not in ARCHIVABLE_STATUSES:
        raise ValueError("unsupported activity status")

    configured_inactive_days = _setting_int(
        "COMMUNITY_ACTIVITY_INACTIVE_DAYS",
        DEFAULT_INACTIVE_DAYS,
    )
    inactive_days = max(
        configured_inactive_days,
        inactive_days or configured_inactive_days,
    )
    required_checks = max(
        2,
        _setting_int(
            "COMMUNITY_ACTIVITY_REQUIRED_CHECKS",
            DEFAULT_REQUIRED_INACTIVE_CHECKS,
        ),
    )
    min_inactive_confidence = _setting_float(
        "COMMUNITY_ACTIVITY_MIN_INACTIVE_CONFIDENCE",
        DEFAULT_MIN_INACTIVE_CONFIDENCE,
    )
    explicit_end_confidence = _setting_float(
        "COMMUNITY_ACTIVITY_EXPLICIT_END_CONFIDENCE",
        DEFAULT_EXPLICIT_END_CONFIDENCE,
    )

    today = timezone.localdate()
    cutoff = today - timedelta(days=inactive_days)
    evidence_urls = list(dict.fromkeys(evidence_urls))[:10]
    required_confidence = (
        explicit_end_confidence
        if status == STATUS_EXPLICITLY_ENDED
        else min_inactive_confidence
    )

    with transaction.atomic():
        community = Community.objects.select_for_update().get(pk=community_id)

        if community.end_at is not None:
            return ArchiveResult(
                community_id=community.pk,
                community_name=community.name,
                action="already_archived",
                archived=True,
                end_at=community.end_at,
            )
        if community.status != "approved" or _is_partner_only(community.tags):
            return ArchiveResult(
                community_id=community.pk,
                community_name=community.name,
                action="ignored_not_eligible",
                archived=False,
                end_at=None,
            )
        if community.updated_at is not None and community.updated_at > cutoff:
            return ArchiveResult(
                community_id=community.pk,
                community_name=community.name,
                action="ignored_recent_metadata_update",
                archived=False,
                end_at=None,
            )

        official_x_handle = extract_x_handle(community.sns_url)
        has_identifier = bool(official_x_handle or community.hashtag_names)
        has_required_evidence = (
            status != STATUS_EXPLICITLY_ENDED
            or (
                bool(official_x_handle)
                and any(
                    extract_x_status_handle(url) == official_x_handle
                    for url in evidence_urls
                )
            )
        )
        evidence_is_sufficient = (
            consecutive_inactive_checks >= required_checks
            and confidence >= required_confidence
            and has_identifier
            and has_required_evidence
        )

        if not evidence_is_sufficient:
            if not has_identifier:
                action = "ignored_missing_identifier"
            elif not has_required_evidence:
                action = "ignored_missing_official_end_evidence"
            else:
                action = "ignored_insufficient_evidence"
            return ArchiveResult(
                community_id=community.pk,
                community_name=community.name,
                action=action,
                archived=False,
                end_at=None,
            )

        community.end_at = today
        # update_fields に updated_at を含めず、監視処理自体を活動実績にしない。
        community.save(update_fields=["end_at"])
        logger.warning(
            "community soft archived: id=%s name=%s status=%s "
            "confidence=%.2f checks=%s evidence=%s",
            community.pk,
            community.name,
            status,
            confidence,
            consecutive_inactive_checks,
            evidence_urls,
        )
        return ArchiveResult(
            community_id=community.pk,
            community_name=community.name,
            action=(
                "archived_explicitly_ended"
                if status == STATUS_EXPLICITLY_ENDED
                else "archived_inactive"
            ),
            archived=True,
            end_at=today,
        )


def extract_x_handle(url: str | None) -> str:
    """X/TwitterプロフィールURLからASCIIハンドルを抽出する。"""
    if not url:
        return ""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
        return ""
    segments = [segment for segment in parsed.path.split("/") if segment]
    if not segments:
        return ""
    handle = segments[0].lstrip("@").lower()
    if handle in _X_RESERVED_PATHS or not _X_HANDLE_PATTERN.fullmatch(handle):
        return ""
    return handle


def extract_x_status_handle(url: str | None) -> str:
    """X/Twitterの個別投稿URLから投稿者ハンドルを抽出する。"""
    if not url:
        return ""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
        return ""
    segments = [segment for segment in parsed.path.split("/") if segment]
    if (
        len(segments) < 3
        or segments[1].lower() != "status"
        or not segments[2].isdigit()
    ):
        return ""
    return extract_x_handle(f"https://x.com/{segments[0]}")


def _is_partner_only(tags) -> bool:
    if isinstance(tags, str):
        normalized = {tags}
    else:
        normalized = set(tags or [])
    return "partner" in normalized and not ({"tech", "academic"} & normalized)


def _date_string(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _setting_int(name: str, default: int) -> int:
    try:
        return int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default


def _setting_float(name: str, default: float) -> float:
    try:
        return float(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default
