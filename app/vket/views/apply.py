from __future__ import annotations

from datetime import datetime, timedelta

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
        formset = self._build_formset(
            collaboration=collaboration,
            participation=participation,
            permissions=permissions,
            user=request.user,
        )
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
                'is_late_lt_submission': self._is_late_lt_submission(
                    collaboration,
                    permissions,
                ),
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
            collaboration=collaboration,
            participation=participation,
            permissions=permissions,
            data=request.POST,
            user=request.user,
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
                    'is_late_lt_submission': self._is_late_lt_submission(
                        collaboration,
                        permissions,
                    ),
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
                    'is_late_lt_submission': self._is_late_lt_submission(
                        collaboration,
                        permissions,
                    ),
                },
            )

        messages.success(request, '参加登録を保存しました。')
        return redirect('vket:status', pk=collaboration.pk)

    def _build_formset(
        self,
        *,
        collaboration: VketCollaboration,
        participation: VketParticipation | None,
        permissions: VketApplyPermissions,
        user,
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
        )
        for form in formset:
            form.can_organizer_delete = True
        if participation:
            presentations = list(participation.presentations.order_by('order', 'id'))
            for form, presentation in zip(formset.forms, presentations):
                form.can_organizer_delete = not presentation.is_organizer_delete_locked

        if not permissions.can_edit_lt:
            self._disable_formset(formset)
            return formset

        if self._is_lt_time_globally_locked(collaboration, user):
            for form in formset:
                form.fields['lt_start_time'].disabled = True
            return formset

        if not self._is_privileged_user(user) and participation:
            for form, presentation in zip(formset.forms, presentations):
                if self._is_presentation_time_locked(presentation):
                    form.fields['lt_start_time'].disabled = True
        return formset

    @staticmethod
    def _is_schedule_locked(participation: VketParticipation | None) -> bool:
        """主催者向けの参加日程を固定する状態なら True を返す"""
        return bool(participation and participation.is_schedule_confirmed)

    @staticmethod
    def _is_privileged_user(user) -> bool:
        """運営管理者なら True を返す。"""
        return user.is_superuser or user.is_staff

    @classmethod
    def _is_lt_time_globally_locked(cls, collaboration: VketCollaboration, user) -> bool:
        """発表情報締切後に非管理者の時刻編集を止める。"""
        return (
            not cls._is_privileged_user(user)
            and timezone.localdate() > collaboration.lt_deadline
        )

    @staticmethod
    def _is_presentation_time_locked(presentation: VketPresentation) -> bool:
        """確定または公開済みの発表時刻を固定する。"""
        return (
            presentation.status == VketPresentation.Status.CONFIRMED
            or presentation.published_event_detail_id is not None
        )

    @staticmethod
    def _is_late_lt_submission(
        collaboration: VketCollaboration,
        permissions: VketApplyPermissions,
    ) -> bool:
        """発表情報締切後も申請として受け付けている状態なら True を返す"""
        return permissions.can_edit_lt and timezone.localdate() > collaboration.lt_deadline

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
            participation.lt_slot_minutes = (
                cleaned.get('lt_slot_minutes')
                or participation.lt_slot_minutes
                or 30
            )

        # 初回申請時にapplied_by/applied_atをセット
        if is_new or participation.progress == VketParticipation.Progress.NOT_APPLIED:
            participation.applied_by = request.user
            participation.applied_at = timezone.now()
            participation.progress = VketParticipation.Progress.APPLIED

        participation.save()

        # プレゼンテーション情報をVketPresentationに保存（formset）
        if permissions.can_edit_lt:
            is_late_lt_submission = self._is_late_lt_submission(collaboration, permissions)
            self._save_presentations(
                participation,
                formset_data,
                is_late_lt_submission=is_late_lt_submission,
                lock_lt_times=self._is_lt_time_globally_locked(collaboration, request.user),
                allow_privileged_time_edits=self._is_privileged_user(request.user),
            )

        return participation

    def _save_presentations(
        self,
        participation: VketParticipation,
        formset_data: list[dict],
        *,
        is_late_lt_submission: bool = False,
        lock_lt_times: bool = False,
        allow_privileged_time_edits: bool = False,
    ) -> None:
        """行ごとの時刻・削除ロックを保ちながらLT情報を保存する。"""
        existing = list(participation.presentations.order_by('order', 'id'))
        saved: list[VketPresentation] = []
        locked_presentation_ids: set[int] = set()

        for index, row in enumerate(formset_data):
            presentation = existing[index] if index < len(existing) else None
            if row.get('DELETE'):
                if presentation and not presentation.is_organizer_delete_locked:
                    presentation.delete()
                elif presentation:
                    if (
                        not allow_privileged_time_edits
                        and self._is_presentation_time_locked(presentation)
                    ):
                        locked_presentation_ids.add(presentation.pk)
                    saved.append(presentation)
                continue

            speaker = (row.get('speaker') or '').strip()
            theme = (row.get('theme') or '').strip()
            if not speaker and not theme:
                if presentation and presentation.is_organizer_delete_locked:
                    if (
                        not allow_privileged_time_edits
                        and self._is_presentation_time_locked(presentation)
                    ):
                        locked_presentation_ids.add(presentation.pk)
                    saved.append(presentation)
                elif presentation:
                    presentation.delete()
                continue

            requested_start_time = row.get('lt_start_time')
            if presentation:
                presentation.speaker = speaker
                presentation.theme = theme
                update_fields = ['speaker', 'theme', 'updated_at']
                time_locked = lock_lt_times or (
                    not allow_privileged_time_edits
                    and self._is_presentation_time_locked(presentation)
                )
                if time_locked:
                    locked_presentation_ids.add(presentation.pk)
                if not time_locked:
                    presentation.requested_start_time = requested_start_time
                    update_fields.append('requested_start_time')
                if is_late_lt_submission:
                    presentation.status = VketPresentation.Status.DRAFT
                    update_fields.append('status')
                presentation.save(update_fields=update_fields)
                saved.append(presentation)
                continue

            presentation = VketPresentation.objects.create(
                participation=participation,
                order=max((item.order for item in existing + saved), default=-1) + 1,
                speaker=speaker,
                theme=theme,
                requested_start_time=None if lock_lt_times else requested_start_time,
                status=VketPresentation.Status.DRAFT,
            )
            saved.append(presentation)

        if not lock_lt_times:
            self._fill_missing_lt_start_times(
                participation,
                saved,
                locked_presentation_ids=locked_presentation_ids,
            )

    @staticmethod
    def _fill_missing_lt_start_times(
        participation: VketParticipation,
        presentations: list[VketPresentation],
        *,
        locked_presentation_ids: set[int],
    ) -> None:
        """未入力の開始時刻を前行または参加枠の開始時刻から補う。"""
        previous_time = None
        for presentation in sorted(presentations, key=lambda item: (item.order, item.id)):
            current_time = presentation.requested_start_time
            if current_time is None and presentation.pk not in locked_presentation_ids:
                base_time = previous_time or participation.requested_start_time
                if base_time is not None:
                    candidate = datetime.combine(timezone.localdate(), base_time)
                    if previous_time is not None:
                        candidate += timedelta(minutes=participation.lt_slot_minutes)
                    if candidate.date() == timezone.localdate():
                        presentation.requested_start_time = candidate.time()
                        presentation.save(update_fields=['requested_start_time', 'updated_at'])
                        current_time = presentation.requested_start_time
            if current_time is not None:
                previous_time = current_time
