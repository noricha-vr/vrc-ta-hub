"""運営スタッフ向けの録画プロキシ確認・開始終了トリミング確定ビュー。"""
from __future__ import annotations

import json
import logging
from typing import Any

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from community.constants import RecordingPolicy
from event.models import EventDetail, RecordingEditOrder, RecordingReviewItem
from event.services.recording_ops.edl import compute_keep_spans, extract_adopted_duration_sec
from event.services.recording_ops.errors import (
    MAX_REVISION,
    RecordingConflictError,
    RecordingNotFoundError,
    RecordingValidationError,
)
from event.services.recording_ops.orders import create_edit_order
from event.services.recording_ops.queries import (
    ORDER_STATUS_FILTER_CHOICES,
    POLICY_FILTER_CHOICES,
    STATUS_FILTER_CHOICES,
    build_recording_detail_context,
    build_recording_list_queryset,
    build_recording_row_summary,
    estimate_remaining_minutes,
    get_recording_list_summary,
)
from event.services.recording_ops.storage import RecordingConsentError
from event.views.recording_review_helpers import (
    build_next_edl_from_registered,
    build_order_log_extra,
    extract_trim_and_cuts_submission,
)
from ta_hub.access_mixins import StaffRequiredMixin

logger = logging.getLogger(__name__)

LIST_PAGINATE_BY = 20

_CONFLICT_MESSAGES: dict[str, str] = {
    "revision_conflict": "他のスタッフまたは処理によってデータが先に更新されました。最新の状態を再読み込みしてからもう一度確認してください。",
    "active_order_exists": "現在この発表の確定指示がキューで処理中（待機中/実行中）のため、完了するまで新しい確定操作はできません。",
    "public_locked": "すでに YouTube で公開済み（public）のため、URL・再生数・記事参照を保持する目的で再差し替えがロックされています。",
    "proxy_expired": "プロキシ動画の保持期限が切れているか残存時間が不足しているため確定できません。",
    "proxy_not_ready": "プロキシ動画がまだ登録されていないため確定できません。",
    "revision_limit_exceeded": "EDL リビジョンの上限に達しているため確定できません。",
}


def _apply_recording_security_headers(response: HttpResponse) -> HttpResponse:
    """署名付き URL・運営編集画面のキャッシュ保存と外部への Referer 送信を防ぐ。

    no-referrer にすると一部ブラウザが同一オリジン POST 時に Origin: null を送り
    本番 HTTPS の CSRF 検証で 403 になるため、クロスオリジンへの Referer 送出を
    遮断しつつ同一オリジンの Origin を保つ same-origin を設定する。
    """
    response["Cache-Control"] = "private, no-store"
    response["Referrer-Policy"] = "same-origin"
    return response


def _build_operator_nav_items(request: HttpRequest, *, current: str = "recordings") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = [
        {"url": reverse("event:my_list"), "label": "イベント管理", "current": False, "operator": False},
    ]
    if request.user.is_staff:
        items.append({"url": reverse("news:list"), "label": "ニュース", "current": False, "operator": True})
    items.append(
        {
            "url": reverse("event:recording_review_list"),
            "label": "録画・YouTube管理",
            "current": current == "recordings",
            "operator": True,
        }
    )
    items.append(
        {
            "url": reverse("announcement:discord_list"),
            "label": "Discord 告知の予約",
            "current": False,
            "operator": True,
        }
    )
    return items


def _wants_json(request: HttpRequest) -> bool:
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    if request.content_type == "application/json":
        return True
    return "application/json" in request.headers.get("Accept", "")


def _human_conflict_message(exc: RecordingConflictError) -> str:
    return _CONFLICT_MESSAGES.get(exc.code, exc.detail)


class RecordingReviewSecurityMixin(StaffRequiredMixin):
    """スタッフ専用録画管理ビュー共通のレスポンスヘッダ保護。"""

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        response = super().dispatch(request, *args, **kwargs)
        return _apply_recording_security_headers(response)


class RecordingReviewListView(RecordingReviewSecurityMixin, View):
    """スタッフ用の撮影動画一覧・フィルタ画面。"""

    template_name = "event/recording_review_list.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        status_filter = request.GET.get("status", "").strip()
        order_filter = request.GET.get("order_status", "").strip()
        policy_filter = request.GET.get("policy", "").strip()
        query = request.GET.get("q", "").strip()

        queryset = build_recording_list_queryset(
            status_filter=status_filter,
            order_filter=order_filter,
            policy_filter=policy_filter,
            query=query,
        )
        paginator = Paginator(queryset, LIST_PAGINATE_BY)
        page_obj = paginator.get_page(request.GET.get("page", "1"))
        rows = [build_recording_row_summary(detail) for detail in page_obj.object_list]
        summary = get_recording_list_summary()

        params = request.GET.copy()
        params.pop("page", None)
        has_filters = any((status_filter, order_filter, policy_filter, query))

        context = {
            "admin_nav_items": _build_operator_nav_items(request, current="recordings"),
            "rows": rows,
            "page_obj": page_obj,
            "summary": summary,
            "current_query_params": params.urlencode(),
            "status_choices": STATUS_FILTER_CHOICES,
            "order_choices": ORDER_STATUS_FILTER_CHOICES,
            "policy_choices": POLICY_FILTER_CHOICES,
            "selected_status": status_filter,
            "selected_order_status": order_filter,
            "selected_policy": policy_filter,
            "search_query": query,
            "has_filters": has_filters,
            "clear_filters_url": reverse("event:recording_review_list"),
        }
        return render(request, self.template_name, context)


class RecordingReviewDetailView(RecordingReviewSecurityMixin, View):
    """スタッフ用の撮影動画プロキシ視聴・開始終了トリミング確定画面。"""

    template_name = "event/recording_review_detail.html"

    def _render_detail(
        self,
        request: HttpRequest,
        detail_id: int,
        *,
        status_code: int = 200,
        conflict_message: str = "",
        conflict_code: str = "",
        error_message: str = "",
        submitted_start_sec: float | None = None,
        submitted_end_sec: float | None = None,
        submitted_cuts: list[dict[str, Any]] | None = None,
    ) -> HttpResponse:
        try:
            detail_ctx = build_recording_detail_context(detail_id, issue_presigned_url=True)
        except RecordingNotFoundError as exc:
            raise Http404(str(exc)) from exc
        except RecordingConsentError:
            return HttpResponseForbidden("撮影公開の同意がない発表の録画確認画面は表示できません。")

        has_conflict = bool(conflict_message)
        can_submit = detail_ctx.can_submit and not has_conflict
        expected_revision_value = "" if has_conflict else str(detail_ctx.edl_revision)
        start_value = submitted_start_sec if submitted_start_sec is not None else detail_ctx.trim_start_sec
        end_value = submitted_end_sec if submitted_end_sec is not None else detail_ctx.trim_end_sec
        cuts_value = submitted_cuts if submitted_cuts is not None else detail_ctx.cuts
        return render(
            request,
            self.template_name,
            {
                "admin_nav_items": _build_operator_nav_items(request, current="recordings"),
                "ctx": detail_ctx,
                "remaining_minutes": estimate_remaining_minutes(detail_ctx.proxy_expires_at),
                "conflict_message": conflict_message,
                "conflict_code": conflict_code,
                "has_conflict": has_conflict,
                "can_submit": can_submit,
                "expected_revision_value": expected_revision_value,
                "error_message": error_message,
                "form_start_sec": start_value,
                "form_end_sec": end_value,
                "form_cuts": cuts_value,
                "form_cuts_json": json.dumps(cuts_value, ensure_ascii=False),
                "max_revision": MAX_REVISION,
            },
            status=status_code,
        )

    def get(self, request: HttpRequest, detail_id: int) -> HttpResponse:
        return self._render_detail(request, detail_id)

    def post(self, request: HttpRequest, detail_id: int) -> HttpResponse:
        wants_json = _wants_json(request)
        detail = (
            EventDetail.objects.select_related("event", "event__community", "recording_review")
            .filter(pk=detail_id, status="approved")
            .first()
        )
        if detail is None:
            if wants_json:
                return JsonResponse({"ok": False, "code": "not_found", "detail": "対象の発表が見つかりません。"}, status=404)
            raise Http404("対象の発表が見つかりません。")

        if detail.recording_policy != RecordingPolicy.PUBLIC or not detail.event.community.recording_allowed:
            logger.warning(
                "recording_proxy_url_denied",
                extra={"event_type": "recording_proxy_url_denied", "event_detail_id": detail.pk, "action": "detail_post", "reason": "consent_not_public"},
            )
            msg = "撮影公開の同意がないため操作できません。"
            if wants_json:
                return JsonResponse({"ok": False, "code": "recording_not_permitted", "detail": msg}, status=403)
            return HttpResponseForbidden(msg)

        try:
            expected_revision, start_sec, end_sec, submitted_cuts = extract_trim_and_cuts_submission(request)
        except RecordingValidationError as exc:
            if wants_json:
                return JsonResponse({"ok": False, "code": exc.code, "detail": exc.detail}, status=400)
            return self._render_detail(request, detail_id, status_code=400, error_message=exc.detail)

        item: RecordingReviewItem | None = getattr(detail, "recording_review", None)
        next_edl = build_next_edl_from_registered(
            item,
            start_sec=start_sec,
            end_sec=end_sec,
            cuts=submitted_cuts,
        )

        try:
            order = create_edit_order(
                detail.pk,
                expected_revision=expected_revision,
                order_type=RecordingEditOrder.OrderType.APPLY_EDL,
                edl_json=next_edl,
                requested_by=request.user,
            )
        except RecordingConflictError as exc:
            conflict_msg = _human_conflict_message(exc)
            logger.warning(
                "recording_review_trim_conflict",
                extra={"event_type": "recording_review_trim_conflict", "event_detail_id": detail.pk, "reason": exc.code},
            )
            if wants_json:
                return JsonResponse(
                    {"ok": False, "code": exc.code, "detail": conflict_msg, "current": exc.current_state},
                    status=409,
                )
            return self._render_detail(
                request,
                detail_id,
                status_code=409,
                conflict_message=conflict_msg,
                conflict_code=exc.code,
                submitted_start_sec=start_sec,
                submitted_end_sec=end_sec,
                submitted_cuts=submitted_cuts,
            )
        except RecordingConsentError as exc:
            if wants_json:
                return JsonResponse({"ok": False, "code": exc.code, "detail": exc.detail}, status=403)
            return HttpResponseForbidden(exc.detail)
        except RecordingValidationError as exc:
            if wants_json:
                return JsonResponse({"ok": False, "code": exc.code, "detail": exc.detail}, status=400)
            return self._render_detail(
                request,
                detail_id,
                status_code=400,
                error_message=exc.detail,
                submitted_start_sec=start_sec,
                submitted_end_sec=end_sec,
                submitted_cuts=submitted_cuts,
            )
        except RecordingNotFoundError as exc:
            if wants_json:
                return JsonResponse({"ok": False, "code": exc.code, "detail": exc.detail}, status=404)
            raise Http404(exc.detail) from exc

        logger.info(
            "recording_review_trim_order_submitted",
            extra=build_order_log_extra(detail.pk, order, next_edl),
        )
        final_cuts = next_edl.get("cuts", []) if next_edl else []
        success_msg = (
            "トリミング・途中カットを確定し、書き出しキューへ登録しました。"
            if final_cuts
            else "トリミングを確定し、書き出しキューへ登録しました。"
        )
        if wants_json:
            return JsonResponse(
                {
                    "ok": True,
                    "order_id": order.pk,
                    "order_status": order.status,
                    "order_status_label": order.get_status_display(),
                    "edl_revision": order.target_revision,
                    "trim": {"start_sec": start_sec, "end_sec": end_sec},
                    "cuts": final_cuts,
                    "keep_spans": [[s, e] for s, e in compute_keep_spans(next_edl)] if next_edl else [],
                    "adopted_duration_sec": extract_adopted_duration_sec(next_edl) if next_edl else 0.0,
                    "message": success_msg,
                },
                status=201,
            )

        messages.success(request, success_msg)
        return redirect("event:recording_review_detail", detail_id=detail.pk)
