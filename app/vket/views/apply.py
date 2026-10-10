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
from ..schedule import (
    blocks_from,
    busy_payload,
    active_blocks,
    find_conflicting_pairs,
    format_pair,
    get_schedule_buffer_minutes,
)
from ..activity import activity_snapshot, notify_activity
from ..services import delete_requested_presentation
from .overlap import warn_overlap
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
                '集会が選択されていません。ヘッダーの「集会一覧」から集会を選択してください。'
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
        return self._render(request, collaboration, community, participation, form, formset, permissions)

    @transaction.atomic
    def post(self, request, pk: int):
        # 自動確定と同じコラボ行を先にロックし、希望の保存途中で確定させない。
        collaboration = get_object_or_404(VketCollaboration.objects.select_for_update(), pk=pk)
        community, membership = _get_active_membership(request)
        if community is None or membership is None:
            return HttpResponseForbidden(
                '集会が選択されていません。ヘッダーの「集会一覧」から集会を選択してください。'
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
            return self._render(
                request, collaboration, community, participation, form, formset, permissions,
            )

        try:
            with transaction.atomic():
                before = activity_snapshot(participation)
                participation = self._save_participation(
                    request=request,
                    collaboration=collaboration,
                    community=community,
                    existing_participation=participation,
                    permissions=permissions,
                    cleaned=form.cleaned_data,
                    formset_data=formset.cleaned_data,
                )
                # 保存した発表で比べる。重なりがあっても保存は取り消さない。
                own_blocks = blocks_from([participation], use_requested=True)
                pairs = find_conflicting_pairs(
                    own_blocks + active_blocks(collaboration, exclude_community_id=community.pk),
                    get_schedule_buffer_minutes(collaboration),
                )
                pair_lines = [
                    format_pair(a, b) for a, b in pairs
                    if community.pk in (a.community_id, b.community_id)
                ]
                after = activity_snapshot(participation)
                notify_activity(collaboration, community.name, before, after, pair_lines)
        except ValueError as e:
            form.add_error(None, str(e))
            return self._render(
                request, collaboration, community, participation, form, formset, permissions,
            )

        messages.success(request, '参加登録を保存しました。')
        warn_overlap(request, '保存', pair_lines, {
            'collaboration_id': collaboration.pk, 'participation_id': participation.pk,
        })
        return redirect('vket:status', pk=collaboration.pk)

    def _render(
        self, request, collaboration, community, participation, form, formset, permissions,
    ):
        """申込みフォームを日程表・空き表示つきで描画する"""
        schedule_ctx = _build_schedule_context(collaboration, include_requested=True)
        busy = None
        if permissions.can_edit_schedule or permissions.can_edit_lt:
            # 日程表で読んだ参加から作り、空き表示のために追加のクエリを出さない
            busy = busy_payload(
                (
                    block
                    for block in schedule_ctx['schedule_blocks']
                    if block.community_id != community.pk
                ),
                buffer_minutes=get_schedule_buffer_minutes(collaboration),
            )
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
                'busy_payload': busy,
                'schedule_buffer_minutes': get_schedule_buffer_minutes(collaboration),
                **schedule_ctx,
            },
        )

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
        if not permissions.can_edit_lt:
            self._disable_formset(formset)
            return formset

        return formset

    def _apply_permissions_for_participation(
        self,
        user,
        collaboration: VketCollaboration,
        participation: VketParticipation | None,
    ) -> VketApplyPermissions:
        """申込み済みの主催者は発表受付のフェーズ内で日程も編集できる。未申請の行と新規受付は従来どおり。"""
        permissions = _apply_permissions_for_user(user, collaboration)
        if participation and participation.progress != VketParticipation.Progress.NOT_APPLIED:
            return VketApplyPermissions(
                can_edit_schedule=permissions.can_edit_lt,
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
            self._save_presentations(
                participation,
                formset_data,
            )

        # prefetch 済みの古い発表一覧を警告・通知で使わない。
        participation._prefetched_objects_cache = {}
        return participation

    def _save_presentations(
        self,
        participation: VketParticipation,
        formset_data: list[dict],
    ) -> None:
        """希望の発表情報を保存する。確定時刻・公開情報は次の確定まで保持する。"""
        existing = list(participation.presentations.order_by('order', 'id'))
        saved: list[VketPresentation] = []
        for index, row in enumerate(formset_data):
            presentation = existing[index] if index < len(existing) else None
            speaker = (row.get('speaker') or '').strip()
            theme = (row.get('theme') or '').strip()
            if row.get('DELETE') or (not speaker and not theme):
                if presentation:
                    delete_requested_presentation(presentation)
                continue
            if presentation:
                presentation.speaker = speaker
                presentation.theme = theme
                presentation.requested_start_time = row.get('lt_start_time')
                presentation.duration = participation.lt_slot_minutes
                presentation.save(update_fields=[
                    'speaker', 'theme', 'requested_start_time', 'duration', 'updated_at',
                ])
            else:
                presentation = VketPresentation.objects.create(
                    participation=participation,
                    order=max((item.order for item in existing + saved), default=-1) + 1,
                    speaker=speaker,
                    theme=theme,
                    requested_start_time=row.get('lt_start_time'),
                    duration=participation.lt_slot_minutes,
                    status=VketPresentation.Status.DRAFT,
                )
            saved.append(presentation)
        self._fill_missing_lt_start_times(participation, saved)

    @staticmethod
    def _fill_missing_lt_start_times(
        participation: VketParticipation,
        presentations: list[VketPresentation],
    ) -> None:
        """未入力の開始時刻を前行または参加枠の開始時刻から補う。"""
        previous_time = None
        for presentation in sorted(presentations, key=lambda item: (item.order, item.id)):
            current_time = presentation.requested_start_time
            if current_time is None:
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
