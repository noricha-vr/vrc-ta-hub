"""Cloud Schedulerから活動監視バッチを起動する内部エンドポイント。"""

import logging
import secrets

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from community.activity_client import ActivityMonitorError
from community.activity_monitor import run_community_activity_checks

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def run_activity_monitor(request):
    """Request-Token認証付きで、期限を迎えた集会活動確認を実行する。"""
    request_token = request.headers.get("Request-Token", "")
    expected_token = settings.REQUEST_TOKEN or ""
    if not expected_token or not secrets.compare_digest(request_token, expected_token):
        return JsonResponse({"error": "Unauthorized"}, status=401)
    if not settings.XAI_API_KEY:
        return JsonResponse({"error": "XAI_API_KEY is not configured"}, status=503)

    try:
        limit = _optional_positive_int(_parameter(request, "limit"), maximum=100)
        community_id = _optional_positive_int(_parameter(request, "community_id"))
        auto_hide_raw = _parameter(request, "auto_hide")
        auto_hide = None if auto_hide_raw is None else _parse_bool(auto_hide_raw)
        summary = run_community_activity_checks(
            dry_run=_parse_bool(_parameter(request, "dry_run"), default=False),
            force=_parse_bool(_parameter(request, "force"), default=False),
            limit=limit,
            community_id=community_id,
            auto_hide=auto_hide,
        )
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except ActivityMonitorError as exc:
        logger.error("活動監視の設定/APIエラー: %s", exc)
        return JsonResponse({"error": str(exc)}, status=503)
    except Exception:
        logger.exception("活動監視バッチの実行に失敗")
        return JsonResponse({"error": "Activity monitor failed. Check logs."}, status=500)

    return JsonResponse(summary, json_dumps_params={"ensure_ascii": False})


def _parameter(request, name: str):
    if name in request.POST:
        return request.POST.get(name)
    return request.GET.get(name)


def _parse_bool(value, *, default: bool | None = None) -> bool:
    if value is None or str(value).strip() == "":
        if default is not None:
            return default
        raise ValueError("boolean parameter is required")
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def _optional_positive_int(value, *, maximum: int | None = None) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid integer value: {value}") from exc
    if parsed < 1:
        raise ValueError("integer parameter must be greater than 0")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"integer parameter must be at most {maximum}")
    return parsed
