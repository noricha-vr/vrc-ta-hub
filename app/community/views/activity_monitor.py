"""ローカル活動監視スクリプト向けの、専用トークン保護API。"""
from __future__ import annotations

import json
import logging
import secrets
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from community.activity_monitoring import (
    DEFAULT_EXPLICIT_END_CONFIDENCE,
    DEFAULT_INACTIVE_DAYS,
    DEFAULT_MIN_INACTIVE_CONFIDENCE,
    DEFAULT_REQUIRED_INACTIVE_CHECKS,
    MAX_CANDIDATES,
    archive_inactive_community,
    get_activity_candidates,
)
from community.models import Community

logger = logging.getLogger(__name__)
MAX_REQUEST_BODY_BYTES = 64 * 1024
_ALLOWED_X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}


@method_decorator(csrf_exempt, name="dispatch")
class CommunityActivityMonitorView(View):
    """活動停止候補の取得と、再検証付きソフトアーカイブを提供する。"""

    http_method_names = ["get", "post"]

    def dispatch(self, request, *args, **kwargs):
        if not _is_authorized(request):
            return _json_response({"detail": "Unauthorized"}, status=401)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        try:
            server_inactive_days = _bounded_int(
                _setting_int(
                    "COMMUNITY_ACTIVITY_INACTIVE_DAYS",
                    DEFAULT_INACTIVE_DAYS,
                ),
                default=DEFAULT_INACTIVE_DAYS,
                minimum=60,
                maximum=365,
            )
            requested_inactive_days = _bounded_int(
                request.GET.get("inactiveDays"),
                default=server_inactive_days,
                minimum=60,
                maximum=365,
            )
            inactive_days = max(server_inactive_days, requested_inactive_days)
            limit = _bounded_int(
                request.GET.get("limit"),
                default=MAX_CANDIDATES,
                minimum=1,
                maximum=MAX_CANDIDATES,
            )
        except ValueError as exc:
            return _json_response({"detail": str(exc)}, status=400)

        candidates = get_activity_candidates(
            inactive_days=inactive_days,
            limit=limit,
        )
        return _json_response(
            {
                "inactiveDays": inactive_days,
                "requiredInactiveChecks": max(
                    2,
                    _setting_int(
                        "COMMUNITY_ACTIVITY_REQUIRED_CHECKS",
                        DEFAULT_REQUIRED_INACTIVE_CHECKS,
                    ),
                ),
                "minInactiveConfidence": _bounded_float_setting(
                    "COMMUNITY_ACTIVITY_MIN_INACTIVE_CONFIDENCE",
                    DEFAULT_MIN_INACTIVE_CONFIDENCE,
                ),
                "explicitEndConfidence": _bounded_float_setting(
                    "COMMUNITY_ACTIVITY_EXPLICIT_END_CONFIDENCE",
                    DEFAULT_EXPLICIT_END_CONFIDENCE,
                ),
                "softArchiveOnly": True,
                "total": len(candidates),
                "communities": [candidate.as_dict() for candidate in candidates],
            }
        )

    def post(self, request):
        if len(request.body) > MAX_REQUEST_BODY_BYTES:
            return _json_response(
                {"detail": "Request body is too large"},
                status=413,
            )

        try:
            payload = json.loads(request.body or b"{}")
            community_id = int(payload["communityId"])
            status = str(payload["status"])
            confidence = float(payload["confidence"])
            consecutive_checks = int(payload["consecutiveInactiveChecks"])
            server_inactive_days = _bounded_int(
                _setting_int(
                    "COMMUNITY_ACTIVITY_INACTIVE_DAYS",
                    DEFAULT_INACTIVE_DAYS,
                ),
                default=DEFAULT_INACTIVE_DAYS,
                minimum=60,
                maximum=365,
            )
            requested_inactive_days = _bounded_int(
                payload.get("inactiveDays"),
                default=server_inactive_days,
                minimum=60,
                maximum=365,
            )
            inactive_days = max(server_inactive_days, requested_inactive_days)
            evidence_urls = _validate_evidence_urls(
                payload.get("evidenceUrls", [])
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return _json_response(
                {"detail": f"Invalid request: {exc}"},
                status=400,
            )

        if not 0.0 <= confidence <= 1.0:
            return _json_response(
                {"detail": "confidence must be between 0 and 1"},
                status=400,
            )
        if not 0 <= consecutive_checks <= 100:
            return _json_response(
                {
                    "detail": (
                        "consecutiveInactiveChecks must be between 0 and 100"
                    )
                },
                status=400,
            )

        try:
            result = archive_inactive_community(
                community_id=community_id,
                status=status,
                confidence=confidence,
                evidence_urls=evidence_urls,
                consecutive_inactive_checks=consecutive_checks,
                inactive_days=inactive_days,
            )
        except Community.DoesNotExist:
            return _json_response({"detail": "Community not found"}, status=404)
        except ValueError as exc:
            return _json_response({"detail": str(exc)}, status=400)
        except Exception:
            logger.exception(
                "community activity archive request failed: community_id=%s",
                community_id,
            )
            return _json_response(
                {"detail": "Failed to apply activity result"},
                status=500,
            )

        return _json_response(result.as_dict())


def _is_authorized(request) -> bool:
    expected = (
        getattr(settings, "COMMUNITY_ACTIVITY_MONITOR_TOKEN", "") or ""
    )
    provided = request.headers.get("Request-Token", "")
    return bool(expected) and secrets.compare_digest(provided, expected)


def _validate_evidence_urls(value) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("evidenceUrls must be an array")
    if len(value) > 10:
        raise ValueError("evidenceUrls accepts at most 10 URLs")

    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or len(item) > 500:
            raise ValueError("evidenceUrls contains an invalid URL")
        parsed = urlsplit(item.strip())
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() not in _ALLOWED_X_HOSTS
            or not _is_x_status_path(parsed.path)
        ):
            raise ValueError(
                "evidenceUrls must contain only HTTPS X/Twitter status URLs"
            )
        clean_url = urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.query, "")
        )
        if clean_url not in normalized:
            normalized.append(clean_url)
    return normalized


def _is_x_status_path(path: str) -> bool:
    segments = [segment for segment in path.split("/") if segment]
    return (
        len(segments) >= 3
        and segments[1].lower() == "status"
        and segments[2].isdigit()
    )


def _json_response(payload: dict, *, status: int = 200) -> JsonResponse:
    response = JsonResponse(payload, status=status)
    response["Cache-Control"] = "no-store"
    return response


def _bounded_int(value, *, default: int, minimum: int, maximum: int) -> int:
    parsed = default if value in (None, "") else int(value)
    if not minimum <= parsed <= maximum:
        raise ValueError(f"value must be between {minimum} and {maximum}")
    return parsed


def _setting_int(name: str, default: int) -> int:
    try:
        return int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default


def _bounded_float_setting(name: str, default: float) -> float:
    try:
        value = float(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default
    return min(max(value, 0.0), 1.0)
