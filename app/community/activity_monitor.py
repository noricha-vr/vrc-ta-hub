"""集会活動判定のバッチ実行・監査保存・自動非表示。"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from ta_hub.index_cache import clear_index_view_cache

from .activity_client import (
    ActivityAssessment,
    XaiActivityClient,
    apply_community_safety_rules,
)
from .activity_models import CommunityActivityCheck, CommunityActivityState
from .activity_notifications import send_activity_notification
from .models import Community

logger = logging.getLogger(__name__)

_CLAIM_STALE_MINUTES = 30


def run_community_activity_checks(
    *,
    dry_run: bool = False,
    force: bool = False,
    limit: int | None = None,
    community_id: int | None = None,
    auto_hide: bool | None = None,
    now: datetime | None = None,
    client: XaiActivityClient | None = None,
) -> dict[str, Any]:
    """期限を迎えた集会を順に確認し、監査履歴・通知・非表示を適用する。"""
    now = now or timezone.now()
    if timezone.is_naive(now):
        now = timezone.make_aware(now, timezone.get_current_timezone())
    today = timezone.localdate(now)
    lookback_days = max(1, int(settings.COMMUNITY_ACTIVITY_LOOKBACK_DAYS))
    from_date = today - timedelta(days=lookback_days - 1)
    batch_limit = max(1, min(int(limit or settings.COMMUNITY_ACTIVITY_BATCH_SIZE), 100))
    should_auto_hide = settings.COMMUNITY_ACTIVITY_AUTO_HIDE if auto_hide is None else auto_hide
    activity_client = client or XaiActivityClient()

    queryset = Community.objects.filter(status="approved", end_at__isnull=True).filter(
        Q(activity_state__isnull=True) | Q(activity_state__monitoring_enabled=True)
    )
    if community_id is not None:
        queryset = queryset.filter(pk=community_id)
    if not force:
        cutoff = now - timedelta(days=max(1, int(settings.COMMUNITY_ACTIVITY_CHECK_INTERVAL_DAYS)))
        queryset = queryset.filter(
            Q(activity_state__isnull=True)
            | Q(activity_state__last_checked_at__isnull=True)
            | Q(activity_state__last_checked_at__lte=cutoff)
        )
    stale_claim = now - timedelta(minutes=_CLAIM_STALE_MINUTES)
    queryset = queryset.filter(
        Q(activity_state__isnull=True)
        | Q(activity_state__check_started_at__isnull=True)
        | Q(activity_state__check_started_at__lte=stale_claim)
    ).order_by(F("activity_state__last_checked_at").asc(nulls_first=True), "pk")

    communities = list(queryset[:batch_limit])
    summary: dict[str, Any] = {
        "dry_run": dry_run,
        "auto_hide": bool(should_auto_hide),
        "from_date": from_date.isoformat(),
        "to_date": today.isoformat(),
        "selected": len(communities),
        "processed": 0,
        "active": 0,
        "inactive": 0,
        "uncertain": 0,
        "errors": 0,
        "warned": 0,
        "hidden": 0,
        "skipped_claimed": 0,
        "cost_in_usd_ticks": 0,
        "results": [],
    }

    for community in communities:
        result = check_one_community(
            community,
            client=activity_client,
            from_date=from_date,
            to_date=today,
            now=now,
            dry_run=dry_run,
            auto_hide=bool(should_auto_hide),
        )
        summary["results"].append(result)
        if result["status"] == "skipped_claimed":
            summary["skipped_claimed"] += 1
            continue
        summary["processed"] += 1
        decision = result.get("decision")
        if decision in {"active", "inactive", "uncertain"}:
            summary[decision] += 1
        if result["status"] == "error":
            summary["errors"] += 1
        if result.get("action") == "warned":
            summary["warned"] += 1
        if result.get("action") == "hidden":
            summary["hidden"] += 1
        summary["cost_in_usd_ticks"] += result.get("cost_in_usd_ticks") or 0

    summary["cost_usd"] = round(summary["cost_in_usd_ticks"] / 10_000_000_000, 6)
    return summary


def check_one_community(
    community: Community,
    *,
    client: XaiActivityClient,
    from_date: date,
    to_date: date,
    now: datetime,
    dry_run: bool,
    auto_hide: bool,
) -> dict[str, Any]:
    state: CommunityActivityState | None = None
    if not dry_run:
        state = claim_activity_state(community, now=now)
        if state is None:
            return {
                "community_id": community.pk,
                "community_name": community.name,
                "status": "skipped_claimed",
                "action": "none",
            }

    try:
        assessment = client.assess(community, from_date=from_date, to_date=to_date)
        min_confidence = min(max(float(settings.COMMUNITY_ACTIVITY_MIN_CONFIDENCE), 0), 1)
        if assessment.decision == "inactive" and assessment.confidence < min_confidence:
            assessment = replace(
                assessment,
                decision="uncertain",
                signal="insufficient_evidence",
                reason=(
                    f"非活動判定の信頼度{assessment.confidence:.3f}が閾値"
                    f"{min_confidence:.3f}未満のため安全側へ変更。元の理由: {assessment.reason}"
                ),
            )
        assessment = apply_community_safety_rules(community, assessment)

        if dry_run:
            action = preview_action(community, assessment, now=now, auto_hide=auto_hide)
        else:
            action = persist_assessment(
                community,
                state=state,
                assessment=assessment,
                now=now,
                auto_hide=auto_hide,
            )
        return {
            "community_id": community.pk,
            "community_name": community.name,
            "status": "ok",
            "decision": assessment.decision,
            "signal": assessment.signal,
            "confidence": assessment.confidence,
            "last_activity_at": (
                assessment.last_activity_at.isoformat() if assessment.last_activity_at else None
            ),
            "reason": assessment.reason,
            "evidence": assessment.evidence,
            "action": action,
            "cost_in_usd_ticks": assessment.cost_in_usd_ticks,
        }
    except Exception as exc:
        logger.exception("集会活動確認に失敗: community_id=%s", community.pk)
        if not dry_run and state is not None:
            record_check_error(community, state=state, error=exc, now=now, model_name=client.model)
        return {
            "community_id": community.pk,
            "community_name": community.name,
            "status": "error",
            "decision": None,
            "action": "none",
            "error": str(exc)[:1000],
        }


def claim_activity_state(community: Community, *, now: datetime) -> CommunityActivityState | None:
    """DBの短期リースを獲得し、Cloud Runの同時実行による二重課金を避ける。"""
    try:
        state, _ = CommunityActivityState.objects.get_or_create(community=community)
    except IntegrityError:
        state = CommunityActivityState.objects.get(community=community)
    stale_claim = now - timedelta(minutes=_CLAIM_STALE_MINUTES)
    claimed = CommunityActivityState.objects.filter(pk=state.pk).filter(
        Q(check_started_at__isnull=True) | Q(check_started_at__lte=stale_claim)
    ).update(check_started_at=now)
    if not claimed:
        return None
    state.refresh_from_db()
    return state


def preview_action(
    community: Community,
    assessment: ActivityAssessment,
    *,
    now: datetime,
    auto_hide: bool,
) -> str:
    if assessment.decision != "inactive":
        return "none"
    state = getattr(community, "activity_state", None)
    if state is None or state.warning_sent_at is None:
        return "would_warn"
    count = state.consecutive_inactive_checks + 1
    required = max(2, int(settings.COMMUNITY_ACTIVITY_REQUIRED_INACTIVE_CHECKS))
    detected_at = state.inactive_detected_at or now
    grace = timedelta(days=max(0, int(settings.COMMUNITY_ACTIVITY_WARNING_DAYS)))
    if auto_hide and count >= required and now >= detected_at + grace:
        return "would_hide"
    return "none"


def persist_assessment(
    community: Community,
    *,
    state: CommunityActivityState,
    assessment: ActivityAssessment,
    now: datetime,
    auto_hide: bool,
) -> str:
    """判定を保存し、必要なら警告またはend_atによる非表示を適用する。"""
    should_warn = False
    action = CommunityActivityCheck.Action.NONE
    today = timezone.localdate(now)
    confidence = Decimal(f"{assessment.confidence:.3f}")

    with transaction.atomic():
        locked_state = CommunityActivityState.objects.select_for_update().get(pk=state.pk)
        locked_community = Community.objects.select_for_update().get(pk=community.pk)
        previously_suspected = locked_state.status == CommunityActivityState.Status.SUSPECTED_INACTIVE

        locked_state.last_checked_at = now
        locked_state.check_started_at = None
        locked_state.last_activity_at = assessment.last_activity_at
        locked_state.last_signal = assessment.signal[:32]
        locked_state.last_confidence = confidence
        locked_state.last_reason = assessment.reason
        locked_state.last_evidence = assessment.evidence
        locked_state.last_response_id = assessment.response_id
        locked_state.last_model_name = assessment.model_name
        locked_state.last_cost_in_usd_ticks = assessment.cost_in_usd_ticks

        if assessment.decision == "active":
            locked_state.status = CommunityActivityState.Status.ACTIVE
            locked_state.consecutive_inactive_checks = 0
            locked_state.inactive_detected_at = None
            locked_state.warning_sent_at = None
            locked_state.auto_hidden_at = None
        elif assessment.decision == "uncertain":
            locked_state.status = CommunityActivityState.Status.UNCERTAIN
            locked_state.consecutive_inactive_checks = 0
            locked_state.inactive_detected_at = None
            locked_state.warning_sent_at = None
            locked_state.auto_hidden_at = None
        else:
            locked_state.consecutive_inactive_checks += 1
            if locked_state.inactive_detected_at is None:
                locked_state.inactive_detected_at = now
            required = max(2, int(settings.COMMUNITY_ACTIVITY_REQUIRED_INACTIVE_CHECKS))
            grace = timedelta(days=max(0, int(settings.COMMUNITY_ACTIVITY_WARNING_DAYS)))
            can_hide = (
                auto_hide
                and locked_state.warning_sent_at is not None
                and locked_state.consecutive_inactive_checks >= required
                and now >= locked_state.inactive_detected_at + grace
            )
            if can_hide:
                locked_community.end_at = today
                locked_community.save(update_fields=["end_at"])
                locked_state.status = CommunityActivityState.Status.INACTIVE
                locked_state.auto_hidden_at = now
                action = CommunityActivityCheck.Action.HIDDEN
            else:
                locked_state.status = CommunityActivityState.Status.SUSPECTED_INACTIVE
                should_warn = locked_state.warning_sent_at is None

        locked_state.save()
        check = CommunityActivityCheck.objects.create(
            community=locked_community,
            result=assessment.decision,
            signal=assessment.signal,
            confidence=confidence,
            last_activity_at=assessment.last_activity_at,
            reason=assessment.reason,
            evidence=assessment.evidence,
            response_id=assessment.response_id,
            model_name=assessment.model_name,
            cost_in_usd_ticks=assessment.cost_in_usd_ticks,
            action=action,
        )

    if should_warn:
        sent = send_activity_notification(
            community,
            assessment=assessment,
            notification_type="warning",
            consecutive_checks=locked_state.consecutive_inactive_checks,
        )
        if sent:
            CommunityActivityState.objects.filter(pk=state.pk, warning_sent_at__isnull=True).update(
                warning_sent_at=now
            )
            CommunityActivityCheck.objects.filter(pk=check.pk).update(
                action=CommunityActivityCheck.Action.WARNED
            )
            action = CommunityActivityCheck.Action.WARNED
    elif action == CommunityActivityCheck.Action.HIDDEN:
        try:
            clear_index_view_cache()
        except Exception:
            logger.exception("自動非表示後のトップページキャッシュ削除に失敗")
        send_activity_notification(
            community,
            assessment=assessment,
            notification_type="hidden",
            consecutive_checks=locked_state.consecutive_inactive_checks,
        )
    elif previously_suspected and assessment.decision == "active":
        logger.info("活動再確認により停止疑いを解除: community_id=%s", community.pk)

    return str(action)


def record_check_error(
    community: Community,
    *,
    state: CommunityActivityState,
    error: Exception,
    now: datetime,
    model_name: str,
) -> None:
    reason = str(error)[:2000]
    with transaction.atomic():
        locked_state = CommunityActivityState.objects.select_for_update().get(pk=state.pk)
        locked_state.status = CommunityActivityState.Status.ERROR
        locked_state.last_checked_at = now
        locked_state.check_started_at = None
        locked_state.last_reason = reason
        # API障害を挟んだまま連続判定として扱わない。
        locked_state.consecutive_inactive_checks = 0
        locked_state.inactive_detected_at = None
        locked_state.warning_sent_at = None
        locked_state.save()
        CommunityActivityCheck.objects.create(
            community=community,
            result=CommunityActivityCheck.Result.ERROR,
            signal="",
            confidence=0,
            reason=reason,
            model_name=model_name,
        )
