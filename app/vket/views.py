from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from itertools import combinations, groupby

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import transaction
from django.db.models import Case, IntegerField, Prefetch, When
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView

from allauth.socialaccount.models import SocialAccount

from community.models import Community, CommunityMember
from event.models import Event, EventDetail

from .forms import VketApplyForm, VketApplyPermissions, VketManageParticipationForm, VketPresentationFormSet
from .models import (
    VketCollaboration,
    VketNotice,
    VketNoticeReceipt,
    VketParticipation,
    VketPresentation,
)

logger = logging.getLogger(__name__)

# フェーズの表示順（数値が小さいほど上位に表示）
PHASE_SORT_ORDER = {
    VketCollaboration.Phase.ENTRY_OPEN: 0,
    VketCollaboration.Phase.SCHEDULING: 1,
    VketCollaboration.Phase.LT_COLLECTION: 2,
    VketCollaboration.Phase.ANNOUNCEMENT: 3,
    VketCollaboration.Phase.LOCKED: 4,
    VketCollaboration.Phase.DRAFT: 5,
    VketCollaboration.Phase.ARCHIVED: 6,
}


def _get_active_membership(request):
    """ログインユーザーのアクティブな集会メンバーシップを返す"""
    if not request.user.is_authenticated:
        return None, None

    memberships = request.user.community_memberships.select_related('community')
    if not memberships.exists():
        return None, None

    community_id = request.session.get('active_community_id')
    membership = memberships.filter(community_id=community_id).first() if community_id else None
    if membership is None:
        membership = memberships.first()
        request.session['active_community_id'] = membership.community_id

    return membership.community, membership


def _apply_permissions_for_user(user, collaboration: VketCollaboration) -> VketApplyPermissions:
    """ユーザーのコラボ操作権限を計算して返す"""
    # スーパーユーザーは全権限を持つ
    if user.is_authenticated and user.is_superuser:
        return VketApplyPermissions(can_edit_schedule=True, can_edit_lt=True)

    today = timezone.localdate()

    # 参加表明（スケジュール）編集: ENTRY_OPENフェーズかつ締切内
    can_edit_schedule = (
        today <= collaboration.registration_deadline
        and collaboration.phase == VketCollaboration.Phase.ENTRY_OPEN
    )

    # LT情報編集: 受付〜LT回収フェーズかつLT締切内
    can_edit_lt = today <= collaboration.lt_deadline and collaboration.phase in {
        VketCollaboration.Phase.ENTRY_OPEN,
        VketCollaboration.Phase.SCHEDULING,
        VketCollaboration.Phase.LT_COLLECTION,
    }

    return VketApplyPermissions(can_edit_schedule=can_edit_schedule, can_edit_lt=can_edit_lt)


def _time_ranges_overlap(
    start1: time, duration1_minutes: int, start2: time, duration2_minutes: int
) -> bool:
    """2つの時間帯が重複するか判定する"""
    base = timezone.localdate()
    s1 = datetime.combine(base, start1)
    e1 = s1 + timedelta(minutes=duration1_minutes)
    s2 = datetime.combine(base, start2)
    e2 = s2 + timedelta(minutes=duration2_minutes)
    return s1 < e2 and s2 < e1


def _shift_time(t: time, delta: timedelta) -> time:
    """時刻を指定分だけシフトする（日跨ぎはエラー）"""
    base = timezone.localdate()
    dt = datetime.combine(base, t) + delta
    if dt.date() != base:
        raise ValueError('時間の変更が日跨ぎになります。')
    return dt.time()




class CollaborationListView(ListView):
    model = VketCollaboration
    template_name = 'vket/collaboration_list.html'
    context_object_name = 'collaborations'

    def get_queryset(self):
        qs = super().get_queryset()
        # スーパーユーザー以外は下書きを除外
        if not (self.request.user.is_authenticated and self.request.user.is_superuser):
            qs = qs.exclude(phase=VketCollaboration.Phase.DRAFT)

        # フェーズ順 → 開催日降順 → ID降順
        all_items = list(qs)
        all_items.sort(
            key=lambda c: (PHASE_SORT_ORDER.get(c.phase, 99), -c.period_start.toordinal(), -c.id)
        )
        # ソート済みIDリストで並び順を維持するためCase式を使う
        id_list = [item.id for item in all_items]
        if not id_list:
            return qs.none()

        preserved_order = Case(
            *[When(pk=pk, then=pos) for pos, pk in enumerate(id_list)],
            output_field=IntegerField(),
        )
        return qs.filter(pk__in=id_list).order_by(preserved_order)


class CollaborationDetailView(DetailView):
    model = VketCollaboration
    template_name = 'vket/collaboration_detail.html'
    context_object_name = 'collaboration'

    def get_queryset(self):
        qs = super().get_queryset()
        # スーパーユーザー以外は下書きを除外
        if not (self.request.user.is_authenticated and self.request.user.is_superuser):
            qs = qs.exclude(phase=VketCollaboration.Phase.DRAFT)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        collaboration: VketCollaboration = context['collaboration']

        lt_details_qs = (
            EventDetail.objects.filter(detail_type='LT', status='approved')
            .only('id', 'event_id', 'speaker', 'theme', 'start_time', 'duration', 'status', 'detail_type')
            .order_by('start_time', 'id')
        )

        # published_event が紐づいている参加のみ表示（公開済み）
        participations = (
            collaboration.participations.filter(published_event__isnull=False)
            .select_related('community', 'published_event')
            .prefetch_related(
                Prefetch('published_event__details', queryset=lt_details_qs)
            )
            .order_by(
                'published_event__date',
                'published_event__start_time',
                'community__name',
            )
        )

        entries = []
        for p in participations:
            # published_event 経由でLT詳細を取得
            lt_detail = next(
                (d for d in p.published_event.details.all() if d.speaker or d.theme),
                None,
            )
            entries.append(
                {'participation': p, 'event': p.published_event, 'lt_detail': lt_detail}
            )

        grouped = []
        for d, group in groupby(entries, key=lambda e: e['event'].date):
            grouped.append({'date': d, 'entries': list(group)})

        context['grouped_entries'] = grouped
        context['scheduled_count'] = len(entries)

        community, membership = _get_active_membership(self.request)
        context['active_community_for_apply'] = community
        is_owner = bool(membership and membership.role == CommunityMember.Role.OWNER)
        participation_exists = False
        participation_has_event = False
        if is_owner and community:
            row = (
                VketParticipation.objects.filter(collaboration=collaboration, community=community)
                .values_list('id', 'published_event_id')
                .first()
            )
            participation_exists = row is not None
            participation_has_event = bool(row and row[1])

        permissions = _apply_permissions_for_user(self.request.user, collaboration)
        # LT編集はpublished_event不要: SCHEDULING/LT_COLLECTIONフェーズではEventなしでもLT情報を入力可能
        context['can_apply'] = bool(
            is_owner
            and (
                permissions.can_edit_schedule
                or (permissions.can_edit_lt and participation_exists)
            )
        )
        context['is_superuser'] = (
            self.request.user.is_authenticated and self.request.user.is_superuser
        )
        context['participation_exists'] = participation_exists
        context['participation_has_event'] = participation_has_event
        return context


class ApplyView(LoginRequiredMixin, View):
    template_name = 'vket/apply.html'

    def get(self, request, pk: int):
        collaboration = get_object_or_404(VketCollaboration, pk=pk)
        community, membership = _get_active_membership(request)
        if community is None or membership is None:
            return HttpResponseForbidden(
                '集会が選択されていません。ヘッダーの「マイ集会」から集会を選択してください。'
            )

        if not (request.user.is_superuser or membership.role == CommunityMember.Role.OWNER):
            return HttpResponseForbidden('主催者のみ参加登録できます。')

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

    def post(self, request, pk: int):
        collaboration = get_object_or_404(VketCollaboration, pk=pk)
        community, membership = _get_active_membership(request)
        if community is None or membership is None:
            return HttpResponseForbidden(
                '集会が選択されていません。ヘッダーの「マイ集会」から集会を選択してください。'
            )

        if not (request.user.is_superuser or membership.role == CommunityMember.Role.OWNER):
            return HttpResponseForbidden('主催者のみ参加登録できます。')

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


class ManageView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'vket/manage.html'

    def test_func(self):
        return self.request.user.is_superuser

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        collaboration = get_object_or_404(VketCollaboration, pk=kwargs['pk'])

        presentations_qs = VketPresentation.objects.select_related(
            'published_event_detail'
        )

        participations = (
            VketParticipation.objects.filter(collaboration=collaboration)
            .select_related('community', 'published_event')
            .prefetch_related(
                Prefetch('presentations', queryset=presentations_qs, to_attr='all_presentations')
            )
            .annotate(
                # 希望日程があるものを上に表示
                sort_has_requested=Case(
                    When(requested_date__isnull=False, then=0),
                    default=1,
                    output_field=IntegerField(),
                )
            )
            .order_by(
                'sort_has_requested',
                'requested_date',
                'requested_start_time',
                'community__name',
            )
        )

        registered_community_ids = set(p.community_id for p in participations)
        all_communities = Community.objects.filter(status='approved').order_by('name')
        unregistered_communities = all_communities.exclude(id__in=registered_community_ids)

        # LT登録数: プレゼンが1件以上ある集会数
        lt_registered_count = sum(
            1 for p in participations if p.all_presentations
        )
        scheduled_count = sum(1 for p in participations if p.confirmed_date)

        # Discordメンションリスト生成（SocialAccountのDiscord UIDから）
        discord_mentions = self._build_discord_mentions(participations)

        context.update(
            {
                'collaboration': collaboration,
                'participations': participations,
                'unregistered_communities': unregistered_communities,
                'community_total': all_communities.count(),
                'participation_count': participations.count(),
                'scheduled_count': scheduled_count,
                'lt_registered_count': lt_registered_count,
                'discord_mentions': discord_mentions,
                # progressラベルの辞書（テンプレートで参照可能）
                'progress_choices': dict(VketParticipation.Progress.choices),
                'lifecycle_choices': dict(VketParticipation.Lifecycle.choices),
                # 管理用の確定時間選択肢
                'duration_choices': [(30, '30分'), (60, '60分'), (90, '90分'), (120, '120分')],
            }
        )
        return context

    @staticmethod
    def _build_discord_mentions(participations) -> dict[str, str]:
        """参加者のDiscordメンション文字列を生成する"""
        active_parts = [
            p for p in participations
            if p.lifecycle == VketParticipation.Lifecycle.ACTIVE and p.applied_by_id
        ]

        user_ids = [p.applied_by_id for p in active_parts]
        discord_accounts = {
            sa.user_id: sa.uid
            for sa in SocialAccount.objects.filter(
                provider='discord', user_id__in=user_ids
            )
        }

        def mentions_for(parts):
            uids = []
            for p in parts:
                uid = discord_accounts.get(p.applied_by_id)
                if uid:
                    uids.append(f'<@{uid}>')
            return ' '.join(uids)

        all_mention = mentions_for(active_parts)

        not_applied = [p for p in active_parts if p.progress == VketParticipation.Progress.NOT_APPLIED]
        stage_not_registered = [p for p in active_parts if p.progress == VketParticipation.Progress.APPLIED]
        lt_not_registered = [p for p in active_parts if p.progress == VketParticipation.Progress.STAGE_REGISTERED]

        return {
            'all': all_mention,
            'not_applied': mentions_for(not_applied),
            'stage_not_registered': mentions_for(stage_not_registered),
            'lt_not_registered': mentions_for(lt_not_registered),
        }


class ManageParticipationUpdateView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        return self.request.user.is_superuser

    def post(self, request, pk: int, participation_id: int):
        collaboration = get_object_or_404(VketCollaboration, pk=pk)
        participation = get_object_or_404(
            VketParticipation.objects.select_related('community'),
            pk=participation_id,
            collaboration=collaboration,
        )

        form = VketManageParticipationForm(
            request.POST,
            participation=participation,
            collaboration=collaboration,
        )

        if not form.is_valid():
            error_messages = []
            for errs in form.errors.values():
                error_messages.extend([str(e) for e in errs])
            messages.error(request, '保存に失敗しました: ' + ' / '.join(error_messages))
            return redirect('vket:manage', pk=collaboration.pk)

        # 確定日程・運営備考の更新
        participation.confirmed_date = form.cleaned_data['confirmed_date']
        participation.confirmed_start_time = form.cleaned_data['confirmed_start_time']
        participation.confirmed_duration = form.cleaned_data['confirmed_duration']
        participation.admin_note = form.cleaned_data.get('admin_note', '')
        participation.schedule_adjusted_by_admin = True
        participation.progress = VketParticipation.Progress.REHEARSAL
        participation.schedule_confirmed_at = timezone.now()

        participation.save(
            update_fields=[
                'confirmed_date',
                'confirmed_start_time',
                'confirmed_duration',
                'admin_note',
                'schedule_adjusted_by_admin',
                'progress',
                'schedule_confirmed_at',
                'updated_at',
            ]
        )

        # DRAFT のLTを一括確定
        participation.presentations.filter(
            status=VketPresentation.Status.DRAFT,
        ).update(status=VketPresentation.Status.CONFIRMED)

        # published_event がある場合、EventDetail 未作成の CONFIRMED LT に EventDetail を作成
        if participation.published_event_id:
            for pres in participation.presentations.filter(
                status=VketPresentation.Status.CONFIRMED,
                published_event_detail__isnull=True,
            ):
                detail = EventDetail.objects.create(
                    event=participation.published_event,
                    theme=pres.theme,
                    speaker=pres.speaker,
                    start_time=pres.confirmed_start_time or pres.requested_start_time or participation.confirmed_start_time,
                    duration=pres.duration,
                    detail_type='LT',
                    status='approved',
                )
                pres.published_event_detail = detail
                pres.save(update_fields=['published_event_detail', 'updated_at'])

        # EventDetailのLT開始時刻を更新
        detail_pattern = re.compile(r'^detail_(\d+)_start_time$')
        detail_updates = {}
        for key, value in request.POST.items():
            m = detail_pattern.match(key)
            if m and value:
                detail_updates[int(m.group(1))] = value

        if detail_updates:
            allowed_detail_ids = set(
                EventDetail.objects.filter(
                    event=participation.published_event
                ).values_list('id', flat=True)
            ) if participation.published_event else set()

            for detail_id, time_str in detail_updates.items():
                if detail_id not in allowed_detail_ids:
                    continue
                try:
                    new_time = datetime.strptime(time_str, '%H:%M').time()
                    EventDetail.objects.filter(pk=detail_id).update(start_time=new_time)
                except (ValueError, KeyError):
                    logger.warning('EventDetail #%d の start_time パース失敗: %s', detail_id, time_str)

        messages.success(
            request,
            f'{participation.community.name} の日程を確定しました。',
        )
        return redirect('vket:manage', pk=collaboration.pk)


@dataclass(frozen=True)
class Slot:
    start: time
    end: time


class ManageScheduleView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'vket/manage_schedule.html'

    def test_func(self):
        return self.request.user.is_superuser

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        collaboration = get_object_or_404(VketCollaboration, pk=kwargs['pk'])

        # confirmed_date+start_time がある参加を表示（公開同期前でも日程表に出す）
        participations = list(
            VketParticipation.objects.filter(
                collaboration=collaboration,
                confirmed_date__isnull=False,
                confirmed_start_time__isnull=False,
            )
            .select_related('community', 'published_event')
            .prefetch_related(
                Prefetch(
                    'published_event__details',
                    queryset=EventDetail.objects.filter(
                        detail_type='LT', status='approved'
                    ).only(
                        'id', 'event_id', 'start_time', 'duration',
                        'speaker', 'theme', 'status', 'detail_type',
                    ).order_by('start_time', 'id'),
                )
            )
            .order_by(
                'confirmed_date',
                'confirmed_start_time',
                'community__name',
            )
        )

        empty_ctx = {
            'collaboration': collaboration,
            'slots': [],
            'rows': [],
            'overlap_warnings': [],
            'warnings': [],
        }

        if not participations:
            context.update(empty_ctx)
            return context

        slot_minutes = 30

        def to_minutes(t: time) -> int:
            return t.hour * 60 + t.minute

        day_minutes = 24 * 60
        min_start = None
        max_end = None
        warnings = []

        # 各参加ごとの LT 開始時刻リスト（participation.id → list[time]）
        lt_times_by_pid: dict[int, list[time]] = {}

        for p in participations:
            p_date = p.confirmed_date
            p_start = p.confirmed_start_time
            p_duration = p.confirmed_duration or 60

            start_min = to_minutes(p_start)
            end_min = start_min + p_duration
            if end_min > day_minutes:
                warnings.append(
                    f'{p_date.strftime("%Y/%m/%d")} {p.community.name} の開催時間が日跨ぎのため、0:00で切り捨て表示します'
                )
                end_min = day_minutes

            # LT 情報: published_event があればそこから取得
            lt_times = []
            if p.published_event:
                lt_details = list(p.published_event.details.all())
                lt_times = [d.start_time for d in lt_details] if lt_details else []
            if not lt_times:
                lt_times = [p_start]
            lt_times_by_pid[p.id] = lt_times

            lt_mins = [to_minutes(t) for t in lt_times]
            lt_min = min(lt_mins)
            lt_end_max = max(min(m + slot_minutes, day_minutes) for m in lt_mins)

            candidate_min = min(start_min, lt_min)
            candidate_max = max(end_min, lt_end_max)
            min_start = candidate_min if min_start is None else min(min_start, candidate_min)
            max_end = candidate_max if max_end is None else max(max_end, candidate_max)

        if min_start is None or max_end is None:
            context.update(empty_ctx | {'warnings': warnings})
            return context

        min_start = (min_start // slot_minutes) * slot_minutes
        max_end = ((max_end + slot_minutes - 1) // slot_minutes) * slot_minutes
        max_end = min(max_end, day_minutes)

        slots: list[Slot] = []
        current = min_start
        base = timezone.localdate()
        while current < max_end:
            s_dt = datetime.combine(base, time(hour=current // 60, minute=current % 60))
            e_dt = s_dt + timedelta(minutes=slot_minutes)
            slots.append(Slot(start=s_dt.time(), end=e_dt.time()))
            current += slot_minutes

        def slot_index_for(t: time) -> int | None:
            delta = to_minutes(t) - min_start
            if delta < 0:
                return None
            idx = delta // slot_minutes
            if idx >= len(slots):
                return None
            return idx

        # 日付×スロットごとの占有数
        occupancy: dict[tuple, int] = {}
        for p in participations:
            p_start = p.confirmed_start_time
            p_duration = p.confirmed_duration or 60
            start_dt = datetime.combine(base, p_start)
            end_dt = start_dt + timedelta(minutes=p_duration)
            for idx, slot in enumerate(slots):
                slot_start = datetime.combine(base, slot.start)
                slot_end = datetime.combine(base, slot.end)
                if slot_start < end_dt and slot_end > start_dt:
                    key = (p.confirmed_date, idx)
                    occupancy[key] = occupancy.get(key, 0) + 1

        rows = []
        overlap_warnings = []

        lt_slots_by_pid: dict[int, dict[int, list[time]]] = {}
        lt_slot_communities: dict[tuple, set[str]] = {}

        for p in participations:
            p_date = p.confirmed_date
            p_start = p.confirmed_start_time
            p_duration = p.confirmed_duration or 60
            lt_times = lt_times_by_pid[p.id]

            event_start_dt = datetime.combine(base, p_start)
            event_end_dt = event_start_dt + timedelta(minutes=p_duration)
            invalid_lt_times = []

            for lt_time in lt_times:
                idx = slot_index_for(lt_time)
                if idx is None:
                    warnings.append(
                        f'{p_date.strftime("%Y/%m/%d")} {p.community.name} のLT開始時刻（{lt_time.strftime("%H:%M")}）が表示範囲外です'
                    )
                    continue

                lt_slots_by_pid.setdefault(p.id, {}).setdefault(idx, []).append(lt_time)
                key = (p_date, idx)
                lt_slot_communities.setdefault(key, set()).add(p.community.name)

                lt_dt = datetime.combine(base, lt_time)
                if not (event_start_dt <= lt_dt < event_end_dt):
                    invalid_lt_times.append(lt_time)

            if invalid_lt_times:
                times_label = ', '.join(sorted({t.strftime('%H:%M') for t in invalid_lt_times}))
                end_time = event_start_dt + timedelta(minutes=p_duration)
                warnings.append(
                    f'{p_date.strftime("%Y/%m/%d")} {p.community.name} のLT開始時刻（{times_label}）が開催時間（{p_start.strftime("%H:%M")}〜{end_time.strftime("%H:%M")}）の範囲外です'
                )

        # 同日に重複する参加のペアワーニング
        for d, group in groupby(participations, key=lambda p: p.confirmed_date):
            parts_on_date = list(group)
            for p1, p2 in combinations(parts_on_date, 2):
                if _time_ranges_overlap(
                    p1.confirmed_start_time, p1.confirmed_duration or 60,
                    p2.confirmed_start_time, p2.confirmed_duration or 60,
                ):
                    overlap_start = max(p1.confirmed_start_time, p2.confirmed_start_time)
                    overlap_warnings.append(
                        f'{d.strftime("%Y/%m/%d")} {overlap_start.strftime("%H:%M")} {p1.community.name} と {p2.community.name} が重複'
                    )

        for (d, idx), communities in sorted(
            lt_slot_communities.items(), key=lambda x: (x[0][0], x[0][1])
        ):
            if len(communities) <= 1:
                continue
            warnings.append(
                f'{d.strftime("%Y/%m/%d")} {slots[idx].start.strftime("%H:%M")} LT開始が重複: {", ".join(sorted(communities))}'
            )

        for p in participations:
            p_start = p.confirmed_start_time
            p_duration = p.confirmed_duration or 60
            base_date = timezone.localdate()
            event_start = datetime.combine(base_date, p_start)
            event_end = event_start + timedelta(minutes=p_duration)
            lt_slots = lt_slots_by_pid.get(p.id, {})
            cells = []
            for idx, slot in enumerate(slots):
                slot_start = datetime.combine(base_date, slot.start)
                slot_end = datetime.combine(base_date, slot.end)
                occupied = slot_start < event_end and slot_end > event_start
                overlap = occupied and occupancy.get((p.confirmed_date, idx), 0) > 1
                lt_times = sorted(lt_slots.get(idx, []))
                lt_overlap = bool(lt_times) and len(lt_slot_communities.get((p.confirmed_date, idx), set())) > 1
                lt_tooltip = ', '.join([t.strftime('%H:%M') for t in lt_times]) if lt_times else ''
                cells.append(
                    {
                        'occupied': occupied,
                        'overlap': overlap,
                        'lt_times': lt_times,
                        'lt_overlap': lt_overlap,
                        'lt_tooltip': lt_tooltip,
                    }
                )
            rows.append({
                'participation': p,
                'date': p.confirmed_date,
                'start_time': p_start,
                'duration': p_duration,
                'cells': cells,
            })

        context.update(
            {
                'collaboration': collaboration,
                'slots': slots,
                'rows': rows,
                'overlap_warnings': overlap_warnings,
                'warnings': warnings,
            }
        )
        return context


def _get_visible_collaborations(user):
    """ユーザーに表示可能なコラボ一覧を返す（ARCHIVED除外、非superuserはDRAFTも除外）"""
    qs = VketCollaboration.objects.exclude(phase=VketCollaboration.Phase.ARCHIVED)
    if not user.is_superuser:
        qs = qs.exclude(phase=VketCollaboration.Phase.DRAFT)
    return qs.order_by('-period_start', '-id')


class VketStatusRedirectView(LoginRequiredMixin, View):
    """pk なしで最新コラボの参加状況ページにリダイレクト"""

    def get(self, request):
        collaboration = _get_visible_collaborations(request.user).first()
        if not collaboration:
            return redirect('vket:list')
        return redirect('vket:status', pk=collaboration.pk)


class StageRegisterView(LoginRequiredMixin, View):
    """主催者がVketステージ登録完了を自己申告するビュー"""

    def post(self, request, pk: int):
        collaboration = get_object_or_404(VketCollaboration, pk=pk)
        community, membership = _get_active_membership(request)
        if community is None or membership is None:
            return HttpResponseForbidden('集会が選択されていません。')

        if not (request.user.is_superuser or membership.role == CommunityMember.Role.OWNER):
            return HttpResponseForbidden('主催者のみステージ登録を完了できます。')

        participation = get_object_or_404(
            VketParticipation,
            collaboration=collaboration,
            community=community,
        )

        # 申請済みの場合のみ登録可能
        if participation.progress != VketParticipation.Progress.APPLIED:
            messages.warning(request, 'ステージ登録は参加申込み後に行ってください。')
            return redirect('vket:status', pk=pk)

        participation.progress = VketParticipation.Progress.STAGE_REGISTERED
        participation.stage_registered_at = timezone.now()
        participation.save(update_fields=['progress', 'stage_registered_at', 'updated_at'])

        messages.success(request, 'Vketステージ登録完了を記録しました。')
        return redirect('vket:status', pk=pk)


class ParticipationStatusView(LoginRequiredMixin, View):
    """主催者向け: 自分の集会のコラボ参加状況を確認するビュー"""

    template_name = 'vket/participation_status.html'

    def get(self, request, pk: int):
        collaboration = get_object_or_404(VketCollaboration, pk=pk)
        community, membership = _get_active_membership(request)

        participation = None
        latest_notices = []
        if community:
            participation = (
                VketParticipation.objects.filter(
                    collaboration=collaboration, community=community
                )
                .prefetch_related(
                    Prefetch(
                        'presentations',
                        queryset=VketPresentation.objects.select_related('published_event_detail'),
                    ),
                    'notice_receipts__notice',
                )
                .first()
            )
            if participation:
                # 最新2件のお知らせ受信記録を取得
                latest_notices = (
                    participation.notice_receipts.select_related('notice')
                    .order_by('-created_at')[:2]
                )

        unacked_count = 0
        if participation:
            unacked_count = participation.notice_receipts.filter(
                notice__requires_ack=True, acknowledged_at__isnull=True
            ).count()

        # progressの選択肢をリスト化してテンプレートに渡す
        progress_steps = [
            {'value': value, 'label': label}
            for value, label in VketParticipation.Progress.choices
        ]

        # コラボ切替ドロップダウン用
        collaborations = list(_get_visible_collaborations(request.user))

        # stage_url を settings_json から取得
        stage_url = None
        if collaboration.settings_json and isinstance(collaboration.settings_json, dict):
            stage_url = collaboration.settings_json.get('stage_url')

        # published_event の EventDetail（LT資料アップロード用、LT情報確定済みのみ）
        event_details = []
        if participation and participation.published_event_id:
            event_details = list(
                EventDetail.objects.filter(
                    event_id=participation.published_event_id,
                    detail_type='LT',
                    status='approved',
                )
                .exclude(speaker='', theme='')
                .order_by('start_time', 'id')
            )

        return render(
            request,
            self.template_name,
            {
                'collaboration': collaboration,
                'participation': participation,
                'community': community,
                'progress_steps': progress_steps,
                'latest_notices': latest_notices,
                'unacked_count': unacked_count,
                'collaborations': collaborations,
                'is_superuser': request.user.is_superuser,
                'stage_url': stage_url,
                'event_details': event_details,
            },
        )


class NoticeListView(LoginRequiredMixin, View):
    """主催者向け: 自分の参加に届いたお知らせ一覧ビュー"""

    template_name = 'vket/notice_list.html'

    def get(self, request, pk: int):
        collaboration = get_object_or_404(VketCollaboration, pk=pk)
        community, membership = _get_active_membership(request)

        receipts = []
        if community:
            participation = VketParticipation.objects.filter(
                collaboration=collaboration, community=community
            ).first()
            if participation:
                receipts = (
                    VketNoticeReceipt.objects.filter(participation=participation)
                    .select_related('notice')
                    .order_by('-created_at')
                )

        return render(
            request,
            self.template_name,
            {
                'collaboration': collaboration,
                'receipts': receipts,
                'community': community,
            },
        )


class ManageNoticeListView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """運営向け: お知らせ管理一覧ビュー"""

    template_name = 'vket/manage_notice_list.html'

    def test_func(self):
        return self.request.user.is_superuser

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        collaboration = get_object_or_404(VketCollaboration, pk=kwargs['pk'])

        # 各お知らせの配送状況を集計
        notices = (
            VketNotice.objects.filter(collaboration=collaboration)
            .prefetch_related('receipts__participation__community')
            .order_by('-created_at')
        )

        # prefetch済みのreceiptsをPython側で集計してN+1を回避
        notice_stats = []
        for notice in notices:
            receipts = list(notice.receipts.all())
            total = len(receipts)
            acked = sum(1 for r in receipts if r.acknowledged_at is not None)

            # 未ACKのコミュニティからDiscordメンション文字列を収集
            unacked_mentions = []
            unacked_community_names = []
            if notice.requires_ack:
                seen_community_ids = set()
                for r in receipts:
                    if r.acknowledged_at is not None:
                        continue
                    community = r.participation.community
                    if community.id in seen_community_ids:
                        continue
                    seen_community_ids.add(community.id)
                    unacked_community_names.append(community.name)
                    mention_type = community.discord_mention_type
                    if mention_type == Community.DiscordMentionType.ROLE and community.discord_mention_role_id:
                        unacked_mentions.append(f'<@&{community.discord_mention_role_id}>')
                    elif mention_type == Community.DiscordMentionType.USERS:
                        for uid in community.discord_mention_user_ids:
                            unacked_mentions.append(f'<@{uid}>')
                    else:
                        # メンション未設定: メンバーのDiscord IDからメンション生成
                        member_user_ids = CommunityMember.objects.filter(
                            community=community
                        ).values_list('user_id', flat=True)
                        discord_ids = SocialAccount.objects.filter(
                            user_id__in=member_user_ids, provider='discord'
                        ).values_list('uid', flat=True)
                        for did in discord_ids:
                            unacked_mentions.append(f'<@{did}>')

            notice_stats.append(
                {
                    'notice': notice,
                    'total': total,
                    'acked': acked,
                    'unacked_mentions': unacked_mentions,
                    'unacked_community_names': unacked_community_names,
                }
            )

        context.update(
            {
                'collaboration': collaboration,
                'notice_stats': notice_stats,
            }
        )
        return context


class ManageNoticeCreateView(LoginRequiredMixin, UserPassesTestMixin, View):
    """運営向け: お知らせ作成ビュー"""

    def test_func(self):
        return self.request.user.is_superuser

    def post(self, request, pk: int):
        collaboration = get_object_or_404(VketCollaboration, pk=pk)
        title = request.POST.get('title', '').strip()
        body = request.POST.get('body', '').strip()
        target_scope = request.POST.get('target_scope', VketNotice.TargetScope.ALL_PARTICIPANTS)
        requires_ack = bool(request.POST.get('requires_ack'))

        if not title or not body:
            messages.error(request, 'タイトルと本文は必須です。')
            return redirect('vket:manage_notice_list', pk=pk)

        if target_scope not in dict(VketNotice.TargetScope.choices):
            target_scope = VketNotice.TargetScope.ALL_PARTICIPANTS

        notice = VketNotice.objects.create(
            collaboration=collaboration,
            title=title,
            body=body,
            target_scope=target_scope,
            requires_ack=requires_ack,
            created_by=request.user,
        )

        # 対象参加者にReceiptを自動生成
        participations = VketParticipation.objects.filter(
            collaboration=collaboration,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
        )
        if target_scope == VketNotice.TargetScope.UNACKED:
            participations = participations.filter(last_acknowledged_at__isnull=True)

        receipts = [
            VketNoticeReceipt(notice=notice, participation=p)
            for p in participations
        ]
        VketNoticeReceipt.objects.bulk_create(receipts)

        messages.success(request, 'お知らせを作成しました。')
        return redirect('vket:manage_notice_list', pk=pk)


class ManageNoticeUpdateView(LoginRequiredMixin, UserPassesTestMixin, View):
    """運営向け: お知らせ編集ビュー"""

    def test_func(self):
        return self.request.user.is_superuser

    def post(self, request, pk: int, notice_id: int):
        collaboration = get_object_or_404(VketCollaboration, pk=pk)
        notice = get_object_or_404(VketNotice, pk=notice_id, collaboration=collaboration)

        title = request.POST.get('title', '').strip()
        body = request.POST.get('body', '').strip()

        if not title or not body:
            messages.error(request, 'タイトルと本文は必須です。')
            return redirect('vket:manage_notice_list', pk=pk)

        notice.title = title
        notice.body = body
        notice.save(update_fields=['title', 'body'])

        messages.success(request, 'お知らせを更新しました。')
        return redirect('vket:manage_notice_list', pk=pk)


class AckNoticeView(View):
    """ログイン不要: お知らせ確認（ACK）ビュー

    GET: お知らせ内容を表示し確認ボタンを提示（状態変更なし）
    POST: 確認済みに変更（Discordプレビュー・スキャナによる誤ACK防止）
    """

    template_name = 'vket/ack_notice.html'

    def get(self, request, ack_token):
        receipt = get_object_or_404(
            VketNoticeReceipt.objects.select_related('notice', 'participation'),
            ack_token=ack_token,
        )
        return render(
            request,
            self.template_name,
            {
                'receipt': receipt,
                'notice': receipt.notice,
                'already_acked': receipt.acknowledged_at is not None,
                'collaboration_id': receipt.participation.collaboration_id,
            },
        )

    def post(self, request, ack_token):
        receipt = get_object_or_404(VketNoticeReceipt, ack_token=ack_token)
        already_acked = receipt.acknowledged_at is not None

        if not already_acked:
            now = timezone.now()
            receipt.acknowledged_at = now
            # ログイン中であれば確認者を記録
            if request.user.is_authenticated:
                receipt.acknowledged_by = request.user
            receipt.save(update_fields=['acknowledged_at', 'acknowledged_by', 'updated_at'])

            # 参加レコードのlast_acknowledged_at/byも更新（進捗管理・監査用）
            participation = receipt.participation
            participation.last_acknowledged_at = now
            if request.user.is_authenticated:
                participation.last_acknowledged_by = request.user
            participation.save(update_fields=['last_acknowledged_at', 'last_acknowledged_by', 'updated_at'])

        return render(
            request,
            self.template_name,
            {
                'receipt': receipt,
                'notice': receipt.notice,
                'already_acked': True,
                'collaboration_id': receipt.participation.collaboration_id,
            },
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
        if not (request.user.is_superuser or membership.role == CommunityMember.Role.OWNER):
            return HttpResponseForbidden('主催者のみLTを削除できます。')

        speaker_name = _delete_presentation(presentation)
        messages.success(request, f'{speaker_name} を削除しました。')
        return redirect('vket:status', pk=pk)


class ManagePresentationDeleteView(LoginRequiredMixin, UserPassesTestMixin, View):
    """管理者用: LTを個別削除する"""

    def test_func(self):
        return self.request.user.is_superuser

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


class ManagePublishView(LoginRequiredMixin, UserPassesTestMixin, View):
    """運営向け: LOCKEDフェーズのコラボをEventとして公開するビュー"""

    def test_func(self):
        return self.request.user.is_superuser

    def post(self, request, pk: int):
        collaboration = get_object_or_404(VketCollaboration, pk=pk)

        # LOCKEDフェーズでないと公開不可
        if collaboration.phase != VketCollaboration.Phase.LOCKED:
            return HttpResponseForbidden(
                'フェーズが「確定」でないため公開処理を実行できません。'
            )

        published_count = 0

        with transaction.atomic():
            for participation in collaboration.participations.filter(
                lifecycle=VketParticipation.Lifecycle.ACTIVE
            ).select_related('community', 'published_event'):
                # confirmed_date/start_time/duration のいずれかが欠けていればスキップ（500回避）
                if not participation.confirmed_date or not participation.confirmed_start_time or not participation.confirmed_duration:
                    logger.warning(
                        '確定日程が不完全のためスキップ',
                        extra={
                            'participation_id': participation.id,
                            'community_name': participation.community.name,
                        },
                    )
                    continue

                weekday_code = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][
                    participation.confirmed_date.weekday()
                ]

                # Event を作成または更新
                # published_event_id がある場合はそのEventを更新し、
                # 無関係のEventを誤って上書きするリスクを回避する（参照: PR実装時の設計）
                if participation.published_event_id:
                    event = participation.published_event
                    Event.objects.filter(pk=event.pk).update(
                        date=participation.confirmed_date,
                        start_time=participation.confirmed_start_time,
                        duration=participation.confirmed_duration,
                        weekday=weekday_code,
                    )
                    event.refresh_from_db()
                else:
                    event = Event.objects.create(
                        community=participation.community,
                        date=participation.confirmed_date,
                        start_time=participation.confirmed_start_time,
                        duration=participation.confirmed_duration,
                        weekday=weekday_code,
                    )

                participation.published_event = event
                participation.progress = VketParticipation.Progress.DONE
                participation.save(update_fields=['published_event', 'progress', 'updated_at'])

                # 確定済みプレゼンテーションを EventDetail として公開
                # speakerをキーにすると空文字重複で上書き衝突するため、
                # published_event_detail_id を優先キーとして update_or_create する
                for pres in participation.presentations.filter(
                    status=VketPresentation.Status.CONFIRMED
                ):
                    detail_defaults = {
                        'event': event,
                        'theme': pres.theme,
                        'speaker': pres.speaker,
                        'start_time': pres.confirmed_start_time or pres.requested_start_time or participation.confirmed_start_time,
                        'duration': pres.duration,
                        'detail_type': 'LT',
                        'status': 'approved',
                    }
                    if pres.published_event_detail_id:
                        # 既存のEventDetailを更新
                        EventDetail.objects.filter(pk=pres.published_event_detail_id).update(
                            **detail_defaults
                        )
                        detail = pres.published_event_detail
                    else:
                        # 新規作成
                        detail = EventDetail.objects.create(**detail_defaults)
                    pres.published_event_detail = detail
                    pres.save(update_fields=['published_event_detail', 'updated_at'])

                published_count += 1
                logger.info(
                    'Vketコラボイベント公開',
                    extra={
                        'collaboration_id': collaboration.id,
                        'participation_id': participation.id,
                        'community_name': participation.community.name,
                        'event_id': event.id,
                    },
                )

        messages.success(
            request,
            f'公開処理完了: {published_count}件のイベントを公開しました。',
        )
        return redirect('vket:manage', pk=collaboration.pk)
