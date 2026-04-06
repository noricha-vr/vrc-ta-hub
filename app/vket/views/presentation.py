from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.views import View

from ..models import (
    VketCollaboration,
    VketPresentation,
)
from .helpers import (
    _get_active_membership,
    _is_vket_admin,
)


def _delete_presentation(presentation: VketPresentation) -> str:
    """プレゼンテーションと関連するEventDetailを削除し、表示名を返す"""
    speaker_name = presentation.speaker or 'LT'
    with transaction.atomic():
        if presentation.published_event_detail:
            presentation.published_event_detail.delete()
        presentation.delete()
    return speaker_name


class PresentationDeleteView(LoginRequiredMixin, View):
    """主催者用: LTを個別削除する"""

    def post(self, request, pk: int, presentation_id: int):
        collaboration = get_object_or_404(VketCollaboration, pk=pk)
        community, membership = _get_active_membership(request)
        presentation = get_object_or_404(
            VketPresentation,
            pk=presentation_id,
            participation__collaboration=collaboration,
        )
        if not community or presentation.participation.community_id != community.id:
            return HttpResponseForbidden('この操作を行う権限がありません。')
        if not (request.user.is_superuser or membership):
            return HttpResponseForbidden('集会メンバーのみLTを削除できます。')

        speaker_name = _delete_presentation(presentation)
        messages.success(request, f'{speaker_name} を削除しました。')
        return redirect('vket:status', pk=pk)


class ManagePresentationDeleteView(LoginRequiredMixin, UserPassesTestMixin, View):
    """管理者用: LTを個別削除する"""

    def test_func(self):
        return _is_vket_admin(self.request.user)

    def post(self, request, pk: int, presentation_id: int):
        collaboration = get_object_or_404(VketCollaboration, pk=pk)
        presentation = get_object_or_404(
            VketPresentation,
            pk=presentation_id,
            participation__collaboration=collaboration,
        )

        speaker_name = _delete_presentation(presentation)
        messages.success(request, f'{speaker_name} を削除しました。')
        return redirect('vket:manage', pk=pk)
