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

        permissions = self._apply_permissions_for_participation(
            request.user,
            collaboration,
            participation,
        )
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
        permissions = self._apply_permissions_for_participation(
            request.user,
            collaboration,
            participation,
        )
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
        formset = self._build_formset(
            participation=participation,
            permissions=permissions,
            data=request.POST,
        )

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
            data,
            initial=lt_initial or None,
            prefix='lt',
            form_kwargs={
                'lock_lt_start_time': self._is_schedule_locked(participation),
            },
        )
        if not permissions.can_edit_lt:
            self._disable_formset(formset)
        return formset

    @staticmethod
    def _is_schedule_locked(participation: VketParticipation | None) -> bool:
        """主催者向けの日程・LT開始時刻を固定する状態なら True を返す"""
        return bool(participation and participation.is_schedule_confirmed)

    def _apply_permissions_for_participation(
        self,
        user,
        collaboration: VketCollaboration,
        participation: VketParticipation | None,
    ) -> VketApplyPermissions:
        """コラボ権限に参加単位の確定後ロックを反映する"""
        permissions = _apply_permissions_for_user(user, collaboration)
        if self._is_schedule_locked(participation) and not user.is_superuser and not user.is_staff:
            return VketApplyPermissions(
                can_edit_schedule=False,
                can_edit_lt=permissions.can_edit_lt,
            )
        return permissions

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
            if self._is_schedule_locked(participation):
                self._save_locked_presentations(participation, formset_data)
            else:
                self._save_editable_presentations(participation, formset_data)

        return participation

    def _save_editable_presentations(
        self,
        participation: VketParticipation,
        formset_data: list[dict],
    ) -> None:
        """確定前のLT情報を通常どおり保存する"""
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

    def _save_locked_presentations(
        self,
        participation: VketParticipation,
        formset_data: list[dict],
    ) -> None:
        """確定後は既存LTの削除と開始時刻変更を拒否して保存する"""
        existing_by_order = {
            pres.order: pres
            for pres in participation.presentations.order_by('order', 'id')
        }
        next_order = max(existing_by_order.keys(), default=-1) + 1

        for index, row in enumerate(formset_data):
            if row.get('DELETE'):
                continue

            speaker = (row.get('speaker') or '').strip()
            theme = (row.get('theme') or '').strip()
            if not speaker and not theme:
                continue

            existing = existing_by_order.get(index)
            if existing:
                existing.speaker = speaker
                existing.theme = theme
                existing.save(update_fields=['speaker', 'theme', 'updated_at'])
                continue

            VketPresentation.objects.create(
                participation=participation,
                order=next_order,
                speaker=speaker,
                theme=theme,
                requested_start_time=row.get('lt_start_time'),
            )
            next_order += 1
