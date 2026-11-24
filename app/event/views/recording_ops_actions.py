"""運営スタッフ向けの録画編集キュー操作（再試行・取り消し・緊急停止）ビュー。"""
from __future__ import annotations

import logging
from collections.abc import Callable

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect
from django.views import View

from event.models import RecordingEditOrder
from event.services.recording_ops.errors import (
    RecordingConflictError,
    RecordingNotFoundError,
    RecordingValidationError,
)
from event.services.recording_ops.order_actions import (
    cancel_edit_order,
    enqueue_manual_stop_publish,
    retry_edit_order,
)
from event.services.recording_ops.storage import RecordingConsentError
from event.views.recording_review import (
    RecordingReviewSecurityMixin,
    _human_conflict_message,
    _wants_json,
    render_recording_detail_response,
)
from event.views.recording_review_helpers import extract_expected_revision

logger = logging.getLogger(__name__)


def _render_form_error_or_fallback(
    request: HttpRequest,
    detail_id: int,
    *,
    status_code: int,
    conflict_message: str = "",
    conflict_code: str = "",
    error_message: str = "",
) -> HttpResponse:
    try:
        response = render_recording_detail_response(
            request,
            detail_id,
            status_code=status_code,
            conflict_message=conflict_message,
            conflict_code=conflict_code,
            error_message=error_message,
        )
    except Http404:
        return HttpResponse(conflict_message or error_message, status=status_code)
    if response.status_code == 403:
        return HttpResponse(conflict_message or error_message, status=status_code)
    return response


def _execute_order_action(
    request: HttpRequest,
    detail_id: int,
    *,
    action_name: str,
    action_fn: Callable[[int], RecordingEditOrder],
    success_status: int,
    success_message_fn: Callable[[RecordingEditOrder], str],
) -> HttpResponse:
    wants_json = _wants_json(request)
    try:
        expected_revision, next_view = extract_expected_revision(request)
    except RecordingValidationError as exc:
        if wants_json:
            return JsonResponse({"ok": False, "code": exc.code, "detail": exc.detail}, status=400)
        return _render_form_error_or_fallback(request, detail_id, status_code=400, error_message=exc.detail)

    try:
        order = action_fn(expected_revision)
    except RecordingConflictError as exc:
        conflict_msg = _human_conflict_message(exc)
        logger.warning(
            "recording_ops_action_conflict",
            extra={
                "event_type": "recording_ops_action_conflict",
                "action": action_name,
                "event_detail_id": detail_id,
                "reason": exc.code,
            },
        )
        if wants_json:
            return JsonResponse(
                {"ok": False, "code": exc.code, "detail": conflict_msg, "current": exc.current_state},
                status=409,
            )
        return _render_form_error_or_fallback(
            request,
            detail_id,
            status_code=409,
            conflict_message=conflict_msg,
            conflict_code=exc.code,
        )
    except RecordingConsentError as exc:
        if wants_json:
            return JsonResponse({"ok": False, "code": exc.code, "detail": exc.detail}, status=403)
        return HttpResponseForbidden(exc.detail)
    except RecordingValidationError as exc:
        if wants_json:
            return JsonResponse({"ok": False, "code": exc.code, "detail": exc.detail}, status=400)
        return _render_form_error_or_fallback(request, detail_id, status_code=400, error_message=exc.detail)
    except RecordingNotFoundError as exc:
        if wants_json:
            return JsonResponse({"ok": False, "code": exc.code, "detail": exc.detail}, status=404)
        raise Http404(exc.detail) from exc

    msg = success_message_fn(order)
    current_revision = order.review_item.edl_revision
    if wants_json:
        return JsonResponse(
            {
                "ok": True,
                "order_id": order.pk,
                "order_type": order.order_type,
                "order_status": order.status,
                "order_status_label": order.get_status_display(),
                "edl_revision": current_revision,
                "message": msg,
            },
            status=success_status,
        )

    messages.success(request, msg)
    if next_view == "list":
        return redirect("event:recording_review_list")
    return redirect("event:recording_review_detail", detail_id=detail_id)


class RecordingOrderRetryView(RecordingReviewSecurityMixin, View):
    """失敗（failed）した録画編集・停止オーダーを再試行する。"""

    def post(self, request: HttpRequest, detail_id: int, order_id: int) -> HttpResponse:
        return _execute_order_action(
            request,
            detail_id,
            action_name="retry_order",
            action_fn=lambda rev: retry_edit_order(
                detail_id,
                order_id,
                expected_revision=rev,
                requested_by=request.user,
            ),
            success_status=201,
            success_message_fn=lambda o: f"指示 #{o.pk}（{o.get_order_type_display()}）を再試行キューへ登録しました。",
        )


class RecordingOrderCancelView(RecordingReviewSecurityMixin, View):
    """待機中（queued）または失敗（failed）のオーダーを取り消す（running 中は 409）。"""

    def post(self, request: HttpRequest, detail_id: int, order_id: int) -> HttpResponse:
        return _execute_order_action(
            request,
            detail_id,
            action_name="cancel_order",
            action_fn=lambda rev: cancel_edit_order(
                detail_id,
                order_id,
                expected_revision=rev,
                requested_by=request.user,
            ),
            success_status=200,
            success_message_fn=lambda o: f"指示 #{o.pk}（{o.get_order_type_display()}）を取り消しました。",
        )


class RecordingStopPublishView(RecordingReviewSecurityMixin, View):
    """公開済みロック中を含む発表に対し、緊急停止（stop_publish）をキューへ登録する。"""

    def post(self, request: HttpRequest, detail_id: int) -> HttpResponse:
        return _execute_order_action(
            request,
            detail_id,
            action_name="stop_publish",
            action_fn=lambda rev: enqueue_manual_stop_publish(
                detail_id,
                expected_revision=rev,
                requested_by=request.user,
            ),
            success_status=201,
            success_message_fn=lambda o: f"緊急停止指示 #{o.pk} をキューへ登録しました。ワーカーが順次非公開化を実行します。",
        )
