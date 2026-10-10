from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.views import View

from ta_hub.access_mixins import AuthenticatedForbiddenMixin

from ..models import (
    VketCollaboration,
    VketPresentation,
)
from ..activity import activity_snapshot, notify_activity
from ..services import delete_requested_presentation
from .helpers import (
    _apply_permissions_for_user,
    _get_active_membership,
    _is_vket_admin,
)


def _delete_presentation(presentation: VketPresentation) -> str:
    """プレゼンテーションと関連するEventDetailを削除し、表示名を返す"""
    speaker_name = presentation.speaker or '発表'
    with transaction.atomic():
        if presentation.published_event_detail:
            presentation.published_event_detail.delete()
        presentation.delete()
    return speaker_name


class PresentationDeleteView(LoginRequiredMixin, View):
    """主催者用: LTを個別削除する"""

    @transaction.atomic
    def post(self, request, pk: int, presentation_id: int):
        collaboration = get_object_or_404(VketCollaboration.objects.select_for_update(), pk=pk)
        community, membership = _get_active_membership(request)
        presentation = get_object_or_404(
            VketPresentation,
            pk=presentation_id,
            participation__collaboration=collaboration,
        )
        if not community or presentation.participation.community_id != community.id:
            return HttpResponseForbidden('この操作を行う権限がありません。')
        if not (request.user.is_superuser or membership):
            return HttpResponseForbidden('集会メンバーのみ発表を削除できます。')
        if not _apply_permissions_for_user(request.user, collaboration).can_edit_lt:
            return HttpResponseForbidden('受付期間外のため編集できません。')

        participation = presentation.participation
        before = activity_snapshot(participation)
        speaker_name = presentation.speaker or '発表'
        delete_requested_presentation(presentation)
        notify_activity(collaboration, community.name, before, activity_snapshot(participation), [])
        messages.success(request, f'{speaker_name} を削除しました。')
        return redirect('vket:status', pk=pk)


class ManagePresentationDeleteView(LoginRequiredMixin, AuthenticatedForbiddenMixin, View):
    """管理者用: LTを個別削除する"""

    def test_func(self):
        return _is_vket_admin(self.request.user)

    @transaction.atomic
    def post(self, request, pk: int, presentation_id: int):
        collaboration = get_object_or_404(VketCollaboration.objects.select_for_update(), pk=pk)
        presentation = get_object_or_404(
            VketPresentation,
            pk=presentation_id,
            participation__collaboration=collaboration,
        )

        speaker_name = _delete_presentation(presentation)
        messages.success(request, f'{speaker_name} を削除しました。')
        return redirect('vket:manage', pk=pk)
