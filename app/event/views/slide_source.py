"""スライド読み込みの再試行。"""
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from event.models import EventDetail, SLIDE_FETCH_FIELDS
from event.services.slide_source import is_importable, mark_slide_fetch_pending
from event.views.helpers import can_manage_event_detail
from user_account.view_modules.lt_applications import _owned_lt_condition


@login_required
@require_POST
def slide_fetch_retry(request, pk):
    with transaction.atomic():
        detail = get_object_or_404(EventDetail.objects.select_for_update(), pk=pk)
        applicant_can_edit = EventDetail.objects.filter(
            _owned_lt_condition(request.user), pk=pk, detail_type='LT',
        ).exclude(status='rejected').exists()
        if not (can_manage_event_detail(request.user, detail) or applicant_can_edit):
            return HttpResponseForbidden()
        if (
            detail.slide_fetch_status == 'failed' and not detail.is_article_ng
            and detail.status != 'rejected' and is_importable(detail.slide_url)
        ):
            mark_slide_fetch_pending(detail)
            detail.save(update_fields=SLIDE_FETCH_FIELDS[1:])
    if request.POST.get('return_to') == 'application' and applicant_can_edit:
        return redirect('account:lt_application_edit', pk=pk)
    return redirect('event:detail_update', pk=pk)
