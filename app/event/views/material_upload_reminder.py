from datetime import datetime, timedelta

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from event.material_upload_reminders import send_material_upload_reminders


def _result_to_dict(result):
    return {
        "eventDetailId": result.event_detail_id,
        "email": result.email,
        "action": result.action,
        "reason": result.reason,
        "confidence": result.confidence,
        "matchedIntent": result.matched_intent,
    }


def _parse_target_date(value):
    if not value:
        return timezone.localdate() - timedelta(days=1), None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date(), None
    except ValueError:
        return None, "date must be YYYY-MM-DD"


@require_http_methods(["GET", "POST"])
def send_material_upload_reminders_view(request):
    """Cloud Scheduler から呼び出し、発表翌日の資料アップロード依頼を送る。"""
    request_token = request.headers.get("Request-Token", "")
    expected = settings.REQUEST_TOKEN or ""
    if not expected or request_token != expected:
        return HttpResponse("Unauthorized", status=401)

    target_date, error = _parse_target_date(request.GET.get("date"))
    if error:
        return JsonResponse({"error": error}, status=400)

    dry_run = request.GET.get("dry_run") == "1" or request.GET.get("dryRun") == "1"
    results = send_material_upload_reminders(target_date=target_date, dry_run=dry_run)
    return JsonResponse(
        {
            "dryRun": dry_run,
            "targetDate": target_date.isoformat(),
            "total": len(results),
            "results": [_result_to_dict(result) for result in results],
        }
    )
