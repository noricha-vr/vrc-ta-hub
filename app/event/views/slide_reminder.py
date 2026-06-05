from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from event.slide_reminders import process_slide_publication_reminders


@require_http_methods(["GET", "POST"])
def send_slide_reminders(request):
    """Cloud Scheduler から呼び出し、資料未公開の発表者へリマインドを送る。"""
    request_token = request.headers.get("Request-Token", "")
    expected = settings.REQUEST_TOKEN or ""
    if not expected or request_token != expected:
        return HttpResponse("Unauthorized", status=401)

    dry_run = request.GET.get("dry_run") == "1" or request.GET.get("dryRun") == "1"
    try:
        limit = int(request.GET.get("limit", "50"))
    except ValueError:
        return JsonResponse({"error": "limit must be an integer"}, status=400)
    result = process_slide_publication_reminders(dry_run=dry_run, limit=limit)
    return JsonResponse(result.as_dict())
