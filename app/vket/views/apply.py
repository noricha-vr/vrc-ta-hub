from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from community.models import Community

from ..forms import VketApplyForm, VketApplyPermissions, VketPresentationFormSet
from ..models import (
    VketCollaboration,
    VketParticipation,
    VketPresentation,
)
from .helpers import (
    _apply_permissions_for_user,
    _build_schedule_context,
    _get_active_membership,
)


class ApplyView(LoginRequiredMixin, View):
    template_name = 'vket/apply.html'

    def get(self, request, pk: int):
        collaboration = get_object_or_404(VketCollaboration, pk=pk)
        community, membership = _get_active_membership(request)
        if community is None or membership is None:
            return HttpResponseForbidden(
                '集会が選択されていません。ヘッダーの「マイ集会」から集会を選択してください。'
            )

        if not (request.user.is_superuser or membership):
            return HttpResponseForbidden('集会メンバーのみ参加登録できます。')

        participation = (
            VketParticipation.objects.filter(collaboration=collaboration, community=community)
            .prefetch_related('presentations')
            .first()
        )

        permissions = _apply_permissions_for_user(request.user, collaboration)
        if participation is None and not permissions.can_edit_schedule:
            return HttpResponseForbidden('受付期間外のため、新規の参加登録はできません。')

        initial = self._build_initial(community, participation)
        form = VketApplyForm(
            collaboration=collaboration,
            community=community,
            participation=participation,
            permissions=permissions,
            initial=initial,
        )
        formset = self._build_formset(participation=participation, permissions=permissions)
        schedule_ctx = _build_schedule_context(collaboration, include_requested=True)
        return render(
            request,
            self.template_name,
            {
                'collaboration': collaboration,
                'community': community,
                'participation': participation,
                'form': form,
                'formset': formset,
                'permissions': permissions,
                **schedule_ctx,
            },
        )

    def post(self, request, pk: int):
        collaboration = get_object_or_404(VketCollaboration, pk=pk)
        community, membership = _get_active_membership(request)
        if community is None or membership is None:
            return HttpResponseForbidden(
                '集会が選択されていません。ヘッダーの「マイ集会」から集会を選択してください。'
            )

        if not (request.user.is_superuser or membership):
            return HttpResponseForbidden('集会メンバーのみ参加登録できます。')

        participation = (
            VketParticipation.objects.filter(collaboration=collaboration, community=community)
            .prefetch_related('presentations')
            .first()
        )
        permissions = _apply_permissions_for_user(request.user, collaboration)
        if participation is None and not permissions.can_edit_schedule:
            return HttpResponseForbidden('受付期間外のため、新規の参加登録はできません。')
        if not permissions.can_edit_schedule and not permissions.can_edit_lt:
            return HttpResponseForbidden('受付期間外のため編集できません。')

        initial = self._build_initial(community, participation)
        form = VketApplyForm(
            request.POST,
            collaboration=collaboration,
            community=community,
            participation=participation,
            permissions=permissions,
            initial=initial,
        )
        formset = VketPresentationFormSet(request.POST, prefix='lt')
        if not permissions.can_edit_lt:
            self._disable_formset(formset)

        if not (form.is_valid() and formset.is_valid()):
            schedule_ctx = _build_schedule_context(collaboration, include_requested=True)
            return render(
                request,
                self.template_name,
                {
                    'collaboration': collaboration,
                    'community': community,
                    'participation': participation,
                    'form': form,
                    'formset': formset,
                    'permissions': permissions,
                    **schedule_ctx,
                },
            )

        try:
            with transaction.atomic():
                participation = self._save_participation(
                    request=request,
                    collaboration=collaboration,
                    community=community,
                    existing_participation=participation,
                    permissions=permissions,
                    cleaned=form.cleaned_data,
                    formset_data=formset.cleaned_data,
                )
        except ValueError as e:
            form.add_error(None, str(e))
            return render(
                request,
                self.template_name,
                {
                    'collaboration': collaboration,
                    'community': community,
                    'participation': participation,
                    'form': form,
                    'formset': formset,
                    'permissions': permissions,
                },
            )

        messages.success(request, '参加登録を保存しました。')
        return redirect('vket:status', pk=collaboration.pk)

    def _build_formset(
        self,
        *,
        participation: VketParticipation | None,
        permissions: VketApplyPermissions,
        data=None,
    ) -> VketPresentationFormSet:
        """LT情報のformsetを構築する"""
        lt_initial = []
        if participation:
            for pres in participation.presentations.order_by('order'):
                lt_initial.append({
                    'speaker': pres.speaker,
                    'theme': pres.theme,
                    'lt_start_time': pres.requested_start_time,
                })

        formset = VketPresentationFormSet(
            data, initial=lt_initial or None, prefix='lt',
        )
        if not permissions.can_edit_lt:
            self._disable_formset(formset)
        return formset

    @staticmethod
    def _disable_formset(formset):
        """formset の全フィールドを disabled にする"""
        for form in formset:
            for field in form.fields.values():
                field.disabled = True

    def _build_initial(
        self, community: Community, participation: VketParticipation | None
    ) -> dict:
        """フォームの初期値を構築する"""
        initial = {
            'requested_start_time': community.start_time,
            'requested_duration': community.duration,
        }

        if participation:
            if participation.requested_date:
                initial['requested_date'] = participation.requested_date
            if participation.requested_start_time:
                initial['requested_start_time'] = participation.requested_start_time
            if participation.requested_duration:
                initial['requested_duration'] = participation.requested_duration
            initial['organizer_note'] = participation.organizer_note
        else:
            initial['organizer_note'] = '当日サポートが欲しい（一人主催の場合）: YES・NO'

        return initial

    def _save_participation(
        self,
        *,
        request,
        collaboration: VketCollaboration,
        community: Community,
        existing_participation: VketParticipation | None,
        permissions: VketApplyPermissions,
        cleaned: dict,
        formset_data: list[dict],
    ) -> VketParticipation:
        """参加情報をDBに保存する（新規作成 or 更新）"""
        is_new = existing_participation is None
        if existing_participation:
            participation = existing_participation
        else:
            participation = VketParticipation(collaboration=collaboration, community=community)

        # 日程情報の保存
        if permissions.can_edit_schedule:
            participation.requested_date = cleaned['requested_date']
            participation.requested_start_time = cleaned['requested_start_time']
            participation.requested_duration = cleaned['requested_duration']

        # 備考情報の保存
        if permissions.can_edit_lt:
            participation.organizer_note = cleaned.get('organizer_note', '')

        # 初回申請時にapplied_by/applied_atをセット
        if is_new or participation.progress == VketParticipation.Progress.NOT_APPLIED:
            participation.applied_by = request.user
            participation.applied_at = timezone.now()
            participation.progress = VketParticipation.Progress.APPLIED

        participation.save()

        # プレゼンテーション情報をVketPresentationに保存（formset）
        if permissions.can_edit_lt:
            saved_orders = set()
            order = 0
            for row in formset_data:
                if row.get('DELETE'):
                    continue
                speaker = (row.get('speaker') or '').strip()
                theme = (row.get('theme') or '').strip()
                if not speaker and not theme:
                    continue
                VketPresentation.objects.update_or_create(
                    participation=participation,
                    order=order,
                    defaults={
                        'speaker': speaker,
                        'theme': theme,
                        'requested_start_time': row.get('lt_start_time'),
                    },
                )
                saved_orders.add(order)
                order += 1
            # formsetで保存されなかったorderの既存レコードを削除
            VketPresentation.objects.filter(
                participation=participation,
            ).exclude(order__in=saved_orders).delete()

        return participation
