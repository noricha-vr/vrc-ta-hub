"""Cloud Scheduler から呼ぶ日程の自動確定。"""
from django.http import HttpResponseForbidden, JsonResponse
from django.views.decorators.http import require_GET

from ta_hub.request_token import is_authorized_request
from ..auto_confirm import auto_confirm_schedules


@require_GET
def run_auto_confirm(request):
    """Request-Token で認証し、一回分の日程確定を行う。"""
    if not is_authorized_request(request):
        return HttpResponseForbidden('Unauthorized')
    return JsonResponse(auto_confirm_schedules())
