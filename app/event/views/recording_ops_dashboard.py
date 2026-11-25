"""運営スタッフ向けの録画運用ダッシュボード画面ビュー。"""
from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views import View

from event.services.recording_ops.dashboard import (
    DEFAULT_FUNNEL_PERIOD,
    build_recording_ops_dashboard_context,
)
from event.views.recording_review import RecordingReviewSecurityMixin, _build_operator_nav_items


class RecordingOpsDashboardView(RecordingReviewSecurityMixin, View):
    """スタッフ用の録画運用ダッシュボード画面（スナップショット統計＋記事ステータス連携）。"""

    template_name = "event/recording_ops_dashboard.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        period = request.GET.get("period", DEFAULT_FUNNEL_PERIOD).strip()
        dashboard = build_recording_ops_dashboard_context(user=request.user, period=period)
        context = {
            "admin_nav_items": _build_operator_nav_items(request, current="recordings"),
            "dashboard": dashboard,
            "review_list_url": reverse("event:recording_review_list"),
        }
        return render(request, self.template_name, context)
