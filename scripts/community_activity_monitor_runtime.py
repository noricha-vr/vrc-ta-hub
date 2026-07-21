"""State transitions, Hub API calls, output, and Discord reporting."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

from community_activity_monitor_common import (
    Assessment,
    MonitorError,
    MonitorResult,
    _escape_discord,
    _parse_date,
    _parse_datetime,
    _reset_inactive_streak,
    _safe_int,
    request_with_retries,
)
from community_activity_monitor_xai import analyze_candidate


def process_candidate(
    *,
    candidate: dict[str, Any],
    state: dict[str, Any],
    session: requests.Session,
    xai_api_key: str,
    model: str,
    reasoning_effort: str,
    base_url: str,
    request_token: str,
    today: date,
    cutoff: date,
    inactive_days: int,
    required_checks: int,
    min_check_interval_days: int,
    max_check_gap_days: int,
    min_inactive_confidence: float,
    explicit_end_confidence: float,
    apply: bool,
) -> MonitorResult:
    community_id = int(candidate["communityId"])
    name = str(candidate.get("name", f"Community {community_id}"))
    state_map = state.setdefault("communities", {})
    saved = dict(state_map.get(str(community_id), {}))
    now = datetime.now(timezone.utc)

    metadata_updated_at = str(candidate.get("metadataUpdatedAt") or "")
    saved_metadata_updated_at = str(saved.get("metadataUpdatedAt") or "")
    if saved_metadata_updated_at and saved_metadata_updated_at != metadata_updated_at:
        _reset_inactive_streak(saved)

    last_inactive_check = _parse_datetime(saved.get("lastInactiveCheckAt"))
    if (
        last_inactive_check is not None
        and now - last_inactive_check > timedelta(days=max_check_gap_days)
    ):
        _reset_inactive_streak(saved)
        last_inactive_check = None

    streak_before = _safe_int(saved.get("inactiveStreak"), 0)
    cached_seen_at = _parse_date(saved.get("lastSeenAt"))

    if cached_seen_at is not None and cached_seen_at >= cutoff:
        assessment = Assessment(
            status="active",
            confidence=1.0,
            latest_activity_date=cached_seen_at,
            summary=(
                "ローカル状態に判定期間内のX活動確認が残っているため、"
                "今回の検索を省略しました。"
            ),
            evidence_urls=list(saved.get("evidenceUrls", []))[:10],
            response_ids=[],
            search_calls=0,
            cost_usd=0.0,
        )
    else:
        try:
            assessment = analyze_candidate(
                session=session,
                xai_api_key=xai_api_key,
                model=model,
                reasoning_effort=reasoning_effort,
                candidate=candidate,
                from_date=cutoff,
                to_date=today,
            )
        except Exception as exc:
            streak_after = 0
            if apply:
                _reset_inactive_streak(saved)
                saved.update(
                    {
                        "metadataUpdatedAt": metadata_updated_at,
                        "lastStatus": "error",
                        "lastCheckedAt": now.isoformat(),
                        "lastSummary": str(exc)[:500],
                    }
                )
                state_map[str(community_id)] = saved
            return MonitorResult(
                community_id=community_id,
                name=name,
                status="error",
                confidence=0.0,
                streak_before=streak_before,
                streak_after=streak_after,
                action="search_error",
                summary="X Searchに失敗したため、連続判定をリセットしました。",
                evidence_urls=[],
                error=str(exc),
            )

    streak_after = streak_before
    action = "no_change"
    has_identifier = bool(
        candidate.get("officialXHandle")
        or candidate.get("twitterHashtags")
    )

    if assessment.status == "active":
        streak_after = 0
        action = "confirmed_active"
        if assessment.latest_activity_date:
            saved["lastSeenAt"] = assessment.latest_activity_date.isoformat()
        _reset_inactive_streak(saved)
    elif assessment.status in {"no_recent_evidence", "explicitly_ended"}:
        required_confidence = (
            explicit_end_confidence
            if assessment.status == "explicitly_ended"
            else min_inactive_confidence
        )
        has_required_evidence = (
            assessment.status != "explicitly_ended"
            or bool(assessment.evidence_urls)
        )
        eligible_inactive = (
            assessment.confidence >= required_confidence
            and has_identifier
            and has_required_evidence
        )
        last_inactive_check = _parse_datetime(saved.get("lastInactiveCheckAt"))
        check_interval_satisfied = (
            last_inactive_check is None
            or now - last_inactive_check
            >= timedelta(days=min_check_interval_days)
        )

        if not eligible_inactive:
            streak_after = 0
            _reset_inactive_streak(saved)
            action = "ignored_low_confidence_or_identifier"
        elif not check_interval_satisfied:
            action = "ignored_check_too_soon"
        else:
            streak_after = streak_before + 1
            saved["lastInactiveCheckAt"] = now.isoformat()
            action = (
                "inactive_warning"
                if streak_after < required_checks
                else "archive_ready"
            )

        if eligible_inactive and streak_after >= required_checks:
            if apply:
                archive_response = request_archive(
                    session=session,
                    base_url=base_url,
                    request_token=request_token,
                    community_id=community_id,
                    status=assessment.status,
                    confidence=assessment.confidence,
                    evidence_urls=assessment.evidence_urls,
                    consecutive_inactive_checks=streak_after,
                    inactive_days=inactive_days,
                )
                action = str(
                    archive_response.get(
                        "action",
                        "archive_response_unknown",
                    )
                )
                if archive_response.get("archived"):
                    saved["archivedAt"] = now.isoformat()
                else:
                    streak_after = 0
                    _reset_inactive_streak(saved)
            else:
                action = "would_archive"
    else:
        streak_after = 0
        _reset_inactive_streak(saved)
        action = "unknown_reset"

    if apply:
        saved.update(
            {
                "metadataUpdatedAt": metadata_updated_at,
                "inactiveStreak": streak_after,
                "lastStatus": assessment.status,
                "lastCheckedAt": now.isoformat(),
                "lastSummary": assessment.summary[:500],
                "evidenceUrls": assessment.evidence_urls[:10],
            }
        )
        state_map[str(community_id)] = saved

    return MonitorResult(
        community_id=community_id,
        name=name,
        status=assessment.status,
        confidence=assessment.confidence,
        streak_before=streak_before,
        streak_after=streak_after,
        action=action,
        summary=assessment.summary,
        evidence_urls=assessment.evidence_urls,
        search_calls=assessment.search_calls,
        cost_usd=assessment.cost_usd,
    )


def fetch_candidates(
    *,
    session: requests.Session,
    base_url: str,
    request_token: str,
    inactive_days: int,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    response = request_with_retries(
        session,
        "GET",
        f"{base_url.rstrip('/')}/community/activity-monitor/",
        headers={"Request-Token": request_token},
        params={"inactiveDays": inactive_days, "limit": limit},
        timeout=30,
    )
    try:
        data = response.json()
    except requests.JSONDecodeError as exc:
        raise MonitorError("candidate API returned non-JSON") from exc
    communities = data.get("communities")
    if not isinstance(communities, list):
        raise MonitorError("candidate API returned an invalid response")
    return communities, data


def request_archive(
    *,
    session: requests.Session,
    base_url: str,
    request_token: str,
    community_id: int,
    status: str,
    confidence: float,
    evidence_urls: list[str],
    consecutive_inactive_checks: int,
    inactive_days: int,
) -> dict[str, Any]:
    response = request_with_retries(
        session,
        "POST",
        f"{base_url.rstrip('/')}/community/activity-monitor/",
        headers={
            "Request-Token": request_token,
            "Content-Type": "application/json",
        },
        json={
            "communityId": community_id,
            "status": status,
            "confidence": confidence,
            "evidenceUrls": evidence_urls,
            "consecutiveInactiveChecks": consecutive_inactive_checks,
            "inactiveDays": inactive_days,
        },
        timeout=30,
    )
    try:
        data = response.json()
    except requests.JSONDecodeError as exc:
        raise MonitorError("archive API returned non-JSON") from exc
    if not isinstance(data, dict):
        raise MonitorError("archive API returned an invalid response")
    return data


def print_result(
    result: MonitorResult,
    *,
    required_checks: int,
) -> None:
    prefix = {
        "confirmed_active": "ACTIVE",
        "inactive_warning": "WARN",
        "would_archive": "DRY",
        "archived_inactive": "ARCHIVE",
        "archived_explicitly_ended": "ARCHIVE",
        "search_error": "ERROR",
    }.get(result.action, "INFO")
    print(
        f"[{prefix}] id={result.community_id} name={result.name!r} "
        f"status={result.status} confidence={result.confidence:.2f} "
        f"streak={result.streak_before}->{result.streak_after}/"
        f"{required_checks} action={result.action} "
        f"x_search_calls={result.search_calls} "
        f"cost=${result.cost_usd:.6f} summary={result.summary}"
    )
    if result.error:
        print(f"  error={result.error}")
    for url in result.evidence_urls[:3]:
        print(f"  evidence={url}")


def summarize_results(results: list[MonitorResult]) -> dict[str, int]:
    summary = {
        "active": 0,
        "warnings": 0,
        "archived": 0,
        "unknown": 0,
        "errors": 0,
    }
    for result in results:
        if result.error or result.action == "search_error":
            summary["errors"] += 1
        elif result.action in {
            "archived_inactive",
            "archived_explicitly_ended",
            "already_archived",
        }:
            summary["archived"] += 1
        elif result.status == "active":
            summary["active"] += 1
        elif result.action in {
            "inactive_warning",
            "archive_ready",
            "would_archive",
        }:
            summary["warnings"] += 1
        else:
            summary["unknown"] += 1
    return summary


def send_discord_summary(
    *,
    session: requests.Session,
    webhook_url: str,
    results: list[MonitorResult],
    apply: bool,
    required_checks: int,
) -> None:
    summary = summarize_results(results)
    mode = "適用" if apply else "ドライラン"
    total_cost_usd = sum(result.cost_usd for result in results)
    header = (
        f"🛰️ **VRC集会 活動監視（{mode}）**\n"
        f"候補 {len(results)} / 活動中 {summary['active']} / "
        f"要注意 {summary['warnings']} / 非表示 {summary['archived']} / "
        f"不明 {summary['unknown']} / エラー {summary['errors']} / "
        f"xAI ${total_cost_usd:.4f}"
    )
    lines = [header]
    noteworthy = [
        result
        for result in results
        if result.action != "confirmed_active" or result.error
    ]
    for result in noteworthy:
        name = _escape_discord(result.name)
        summary_text = _escape_discord(result.summary)
        line = (
            f"\n• **{name}** — `{result.action}` / "
            f"{result.confidence:.2f} / "
            f"{result.streak_after}/{required_checks}\n  {summary_text}"
        )
        if result.evidence_urls:
            line += f"\n  {result.evidence_urls[0]}"
        if result.error:
            line += (
                f"\n  error: `{_escape_discord(result.error[:300])}`"
            )
        lines.append(line)

    for chunk in _chunk_discord_content(lines, max_length=1900):
        response = request_with_retries(
            session,
            "POST",
            webhook_url,
            json={
                "content": chunk,
                "allowed_mentions": {"parse": []},
            },
            timeout=20,
        )
        if response.status_code not in {200, 204}:
            raise MonitorError(
                f"Discord webhook returned HTTP {response.status_code}"
            )


def send_failure_notification(
    session: requests.Session,
    webhook_url: str,
    error: str,
) -> None:
    try:
        request_with_retries(
            session,
            "POST",
            webhook_url,
            json={
                "content": (
                    "🚨 **VRC集会 活動監視に失敗**\n"
                    f"`{_escape_discord(error[:1200])}`"
                ),
                "allowed_mentions": {"parse": []},
            },
            timeout=20,
        )
    except Exception:
        pass


def _chunk_discord_content(
    lines: list[str],
    *,
    max_length: int,
) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in lines:
        if len(line) > max_length:
            line = line[: max_length - 3] + "..."
        candidate = current + line
        if current and len(candidate) > max_length:
            chunks.append(current)
            current = line.lstrip("\n")
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or ["VRC集会 活動監視: 対象なし"]
