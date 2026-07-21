#!/usr/bin/env python3
"""VRC集会の活動をGrok X Searchで確認し、安全にソフトアーカイブする。"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

# spec_from_file_location を使うDjangoテストから読み込む場合も、兄弟モジュールを解決する。
SCRIPT_DIR = Path(__file__).resolve().parent


if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import community_activity_monitor_xai as xai
from community_activity_monitor_common import (
    Assessment,
    DEFAULT_BASE_URL,
    DEFAULT_EXPLICIT_END_CONFIDENCE,
    DEFAULT_INACTIVE_DAYS,
    DEFAULT_MAX_CHECK_GAP_DAYS,
    DEFAULT_MIN_CHECK_INTERVAL_DAYS,
    DEFAULT_MIN_INACTIVE_CONFIDENCE,
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_REQUIRED_CHECKS,
    DEFAULT_STATE_FILE,
    MonitorError,
    MonitorResult,
    StateStore,
    _env_float,
    _env_int,
    _env_int_list,
    intersect_evidence_urls,
    is_discord_webhook_url,
    redact_url_for_logs,
)
from community_activity_monitor_runtime import (
    fetch_candidates,
    print_result,
    process_candidate,
    send_discord_summary,
    send_failure_notification,
    summarize_results,
)

# テストや手動検証で従来どおりこのエントリーモジュールから呼べるよう再公開する。
__all__ = [
    "Assessment",
    "analyze_candidate",
    "call_x_search",
    "intersect_evidence_urls",
    "redact_url_for_logs",
]
call_x_search = xai.call_x_search


def analyze_candidate(**kwargs):
    return xai.analyze_candidate(search_func=call_x_search, **kwargs)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "VRC集会の活動状況をGrok X Searchで確認します。既定はドライランです。"
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="状態を保存し、連続判定成立時に本番サイトを非表示化します。",
    )
    parser.add_argument(
        "--notify-dry-run",
        action="store_true",
        help="ドライランでもDiscordへ結果を送信します。",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Discord通知を無効化します。",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("VRC_TA_HUB_BASE_URL", DEFAULT_BASE_URL),
    )
    parser.add_argument(
        "--model",
        default=os.getenv("COMMUNITY_ACTIVITY_XAI_MODEL", DEFAULT_MODEL),
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=["low", "medium", "high"],
        default=os.getenv(
            "COMMUNITY_ACTIVITY_REASONING_EFFORT",
            DEFAULT_REASONING_EFFORT,
        ),
    )
    parser.add_argument(
        "--inactive-days",
        type=int,
        default=_env_int(
            "COMMUNITY_ACTIVITY_INACTIVE_DAYS",
            DEFAULT_INACTIVE_DAYS,
        ),
    )
    parser.add_argument(
        "--required-checks",
        type=int,
        default=_env_int(
            "COMMUNITY_ACTIVITY_REQUIRED_CHECKS",
            DEFAULT_REQUIRED_CHECKS,
        ),
    )
    parser.add_argument(
        "--min-check-interval-days",
        type=int,
        default=_env_int(
            "COMMUNITY_ACTIVITY_MIN_CHECK_INTERVAL_DAYS",
            DEFAULT_MIN_CHECK_INTERVAL_DAYS,
        ),
    )
    parser.add_argument(
        "--max-check-gap-days",
        type=int,
        default=_env_int(
            "COMMUNITY_ACTIVITY_MAX_CHECK_GAP_DAYS",
            DEFAULT_MAX_CHECK_GAP_DAYS,
        ),
    )
    parser.add_argument(
        "--min-inactive-confidence",
        type=float,
        default=_env_float(
            "COMMUNITY_ACTIVITY_MIN_INACTIVE_CONFIDENCE",
            DEFAULT_MIN_INACTIVE_CONFIDENCE,
        ),
    )
    parser.add_argument(
        "--explicit-end-confidence",
        type=float,
        default=_env_float(
            "COMMUNITY_ACTIVITY_EXPLICIT_END_CONFIDENCE",
            DEFAULT_EXPLICIT_END_CONFIDENCE,
        ),
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=_env_int("COMMUNITY_ACTIVITY_MAX_CANDIDATES", 200),
    )
    parser.add_argument(
        "--community-id",
        dest="community_ids",
        type=int,
        action="append",
        help="指定IDだけを処理します。複数回指定できます。",
    )
    parser.add_argument(
        "--exclude-community-id",
        dest="excluded_community_ids",
        type=int,
        action="append",
        default=_env_int_list("COMMUNITY_ACTIVITY_EXCLUDED_IDS"),
        help="自動判定から除外する集会ID。複数回指定できます。",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(
            os.getenv(
                "COMMUNITY_ACTIVITY_STATE_FILE",
                str(DEFAULT_STATE_FILE),
            )
        ).expanduser(),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    _validate_args(args)

    request_token = os.getenv("VRC_TA_HUB_ACTIVITY_TOKEN", "")
    xai_api_key = os.getenv("XAI_API_KEY", "")
    webhook_url = os.getenv("COMMUNITY_ACTIVITY_DISCORD_WEBHOOK_URL", "")
    if not request_token:
        raise MonitorError("VRC_TA_HUB_ACTIVITY_TOKEN is required")
    if not xai_api_key:
        raise MonitorError("XAI_API_KEY is required")
    if webhook_url and not is_discord_webhook_url(webhook_url):
        raise MonitorError(
            "COMMUNITY_ACTIVITY_DISCORD_WEBHOOK_URL is not a Discord webhook URL"
        )

    session = requests.Session()
    session.headers.update(
        {"User-Agent": "vrc-ta-hub-community-activity-monitor/1.0"}
    )
    state_store = StateStore(args.state_file)

    with state_store.locked():
        state = state_store.load()
        try:
            candidates, server_config = fetch_candidates(
                session=session,
                base_url=args.base_url,
                request_token=request_token,
                inactive_days=args.inactive_days,
                limit=args.max_candidates,
            )
        except Exception as exc:
            if webhook_url and not args.no_notify:
                send_failure_notification(session, webhook_url, str(exc))
            raise

        if args.community_ids:
            selected = set(args.community_ids)
            candidates = [
                item
                for item in candidates
                if int(item["communityId"]) in selected
            ]
        excluded = set(args.excluded_community_ids)
        candidates = [
            item
            for item in candidates
            if int(item["communityId"]) not in excluded
        ]

        required_checks = max(
            args.required_checks,
            int(server_config.get("requiredInactiveChecks", 2)),
        )
        min_inactive_confidence = max(
            args.min_inactive_confidence,
            float(
                server_config.get(
                    "minInactiveConfidence",
                    DEFAULT_MIN_INACTIVE_CONFIDENCE,
                )
            ),
        )
        explicit_end_confidence = max(
            args.explicit_end_confidence,
            float(
                server_config.get(
                    "explicitEndConfidence",
                    DEFAULT_EXPLICIT_END_CONFIDENCE,
                )
            ),
        )

        effective_inactive_days = max(
            args.inactive_days,
            int(server_config.get("inactiveDays", args.inactive_days)),
        )
        results: list[MonitorResult] = []
        today = datetime.now().astimezone().date()
        cutoff = today - timedelta(days=effective_inactive_days)
        for candidate in candidates:
            result = process_candidate(
                candidate=candidate,
                state=state,
                session=session,
                xai_api_key=xai_api_key,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                base_url=args.base_url,
                request_token=request_token,
                today=today,
                cutoff=cutoff,
                inactive_days=effective_inactive_days,
                required_checks=required_checks,
                min_check_interval_days=args.min_check_interval_days,
                max_check_gap_days=args.max_check_gap_days,
                min_inactive_confidence=min_inactive_confidence,
                explicit_end_confidence=explicit_end_confidence,
                apply=args.apply,
            )
            results.append(result)
            print_result(result, required_checks=required_checks)

        if args.apply:
            state["updatedAt"] = datetime.now(timezone.utc).isoformat()
            state_store.save(state)

    summary = summarize_results(results)
    total_search_calls = sum(result.search_calls for result in results)
    total_cost_usd = sum(result.cost_usd for result in results)
    print(
        "summary: candidates={candidates} active={active} warnings={warnings} "
        "archived={archived} unknown={unknown} errors={errors} "
        "x_search_calls={calls} xai_cost_usd=${cost:.6f}".format(
            candidates=len(results),
            active=summary["active"],
            warnings=summary["warnings"],
            archived=summary["archived"],
            unknown=summary["unknown"],
            errors=summary["errors"],
            calls=total_search_calls,
            cost=total_cost_usd,
        )
    )

    should_notify = (
        not args.no_notify
        and bool(webhook_url)
        and (args.apply or args.notify_dry_run)
    )
    if should_notify:
        send_discord_summary(
            session=session,
            webhook_url=webhook_url,
            results=results,
            apply=args.apply,
            required_checks=required_checks,
        )
    elif (
        not webhook_url
        and (args.apply or args.notify_dry_run)
        and not args.no_notify
    ):
        print(
            "warning: COMMUNITY_ACTIVITY_DISCORD_WEBHOOK_URL is not set",
            file=sys.stderr,
        )

    return 1 if summary["errors"] else 0


def _validate_args(args: argparse.Namespace) -> None:
    if not 60 <= args.inactive_days <= 365:
        raise MonitorError("--inactive-days must be between 60 and 365")
    if not 2 <= args.required_checks <= 10:
        raise MonitorError("--required-checks must be between 2 and 10")
    if not 1 <= args.min_check_interval_days <= 60:
        raise MonitorError(
            "--min-check-interval-days must be between 1 and 60"
        )
    if not args.min_check_interval_days < args.max_check_gap_days <= 180:
        raise MonitorError(
            "--max-check-gap-days must be greater than the minimum interval "
            "and at most 180"
        )
    if not 0.5 <= args.min_inactive_confidence <= 1.0:
        raise MonitorError(
            "--min-inactive-confidence must be between 0.5 and 1.0"
        )
    if not 0.5 <= args.explicit_end_confidence <= 1.0:
        raise MonitorError(
            "--explicit-end-confidence must be between 0.5 and 1.0"
        )
    if not 1 <= args.max_candidates <= 200:
        raise MonitorError("--max-candidates must be between 1 and 200")

    parsed_base_url = urlsplit(args.base_url)
    is_localhost = parsed_base_url.hostname in {"localhost", "127.0.0.1"}
    if (
        not parsed_base_url.hostname
        or parsed_base_url.username
        or parsed_base_url.password
        or (parsed_base_url.scheme != "https" and not is_localhost)
    ):
        raise MonitorError(
            "--base-url must use HTTPS and must not contain credentials "
            "(localhost is allowed for testing)"
        )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        raise SystemExit(1)
