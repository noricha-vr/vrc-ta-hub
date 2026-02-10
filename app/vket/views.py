from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from itertools import combinations, groupby

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import IntegrityError, transaction
from django.db.models import Case, IntegerField, Prefetch, When
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView

from community.models import Community, CommunityMember
from event.models import Event, EventDetail

from .forms import VketApplyForm, VketApplyPermissions, VketManageEventEditForm
from .models import VketCollaboration, VketParticipation


def _get_active_membership(request):
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
    if user.is_authenticated and user.is_superuser:
        return VketApplyPermissions(can_edit_schedule=True, can_edit_lt=True)

    today = timezone.localdate()
    can_edit_schedule = today <= collaboration.registration_deadline and collaboration.status == VketCollaboration.Status.OPEN
    can_edit_lt = today <= collaboration.lt_deadline and collaboration.status in {
        VketCollaboration.Status.OPEN,
        VketCollaboration.Status.CLOSED,
    }
    return VketApplyPermissions(can_edit_schedule=can_edit_schedule, can_edit_lt=can_edit_lt)


def _time_ranges_overlap(start1: time, duration1_minutes: int, start2: time, duration2_minutes: int) -> bool:
    base = timezone.localdate()
    s1 = datetime.combine(base, start1)
    e1 = s1 + timedelta(minutes=duration1_minutes)
    s2 = datetime.combine(base, start2)
    e2 = s2 + timedelta(minutes=duration2_minutes)
    return s1 < e2 and s2 < e1


def _shift_time(t: time, delta: timedelta) -> time:
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
        if not (self.request.user.is_authenticated and self.request.user.is_superuser):
            qs = qs.exclude(status=VketCollaboration.Status.DRAFT)
        return qs.order_by(
            Case(
                When(status=VketCollaboration.Status.OPEN, then=0),
                When(status=VketCollaboration.Status.CLOSED, then=1),
                When(status=VketCollaboration.Status.DRAFT, then=2),
                When(status=VketCollaboration.Status.ARCHIVED, then=3),
                default=99,
                output_field=IntegerField(),
            ),
            '-period_start',
            '-id',
        )


class CollaborationDetailView(DetailView):
    model = VketCollaboration
    template_name = 'vket/collaboration_detail.html'
    context_object_name = 'collaboration'

    def get_queryset(self):
        qs = super().get_queryset()
        if not (self.request.user.is_authenticated and self.request.user.is_superuser):
            qs = qs.exclude(status=VketCollaboration.Status.DRAFT)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        collaboration: VketCollaboration = context['collaboration']

        lt_details_qs = (
            EventDetail.objects.filter(detail_type='LT', status='approved')
            .only('id', 'event_id', 'speaker', 'theme', 'start_time', 'duration', 'status', 'detail_type')
            .order_by('start_time', 'id')
        )

        participations = (
            collaboration.participations.filter(event__isnull=False)
            .select_related('community', 'event')
            .prefetch_related(Prefetch('event__details', queryset=lt_details_qs))
            .order_by('event__date', 'event__start_time', 'community__name')
        )

        entries = []
        for p in participations:
            lt_detail = next((d for d in p.event.details.all() if d.speaker or d.theme), None)
            entries.append({'participation': p, 'event': p.event, 'lt_detail': lt_detail})

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
                .values_list('id', 'event_id')
                .first()
            )
            participation_exists = row is not None
            participation_has_event = bool(row and row[1])

        permissions = _apply_permissions_for_user(self.request.user, collaboration)
        context['can_apply'] = bool(
            is_owner and (permissions.can_edit_schedule or (permissions.can_edit_lt and participation_has_event))
        )
        context['is_superuser'] = self.request.user.is_authenticated and self.request.user.is_superuser
        context['participation_exists'] = participation_exists
        context['participation_has_event'] = participation_has_event
        return context


class ApplyView(LoginRequiredMixin, View):
    template_name = 'vket/apply.html'

    def get(self, request, pk: int):
        collaboration = get_object_or_404(VketCollaboration, pk=pk)
        community, membership = _get_active_membership(request)
        if community is None or membership is None:
            return HttpResponseForbidden('集会が選択されていません。ヘッダーの「マイ集会」から集会を選択してください。')

        if not (request.user.is_superuser or membership.role == CommunityMember.Role.OWNER):
            return HttpResponseForbidden('主催者のみ参加登録できます。')

        participation = (
            VketParticipation.objects.filter(collaboration=collaboration, community=community)
            .select_related('event')
            .first()
        )

        permissions = _apply_permissions_for_user(request.user, collaboration)
        if participation is None and not permissions.can_edit_schedule:
            return HttpResponseForbidden('受付期間外のため、新規の参加登録はできません。')
        if participation is not None and participation.event_id is None and not permissions.can_edit_schedule:
            return HttpResponseForbidden('日程が未確定のため、受付期間外の編集はできません。運営に日程登録を依頼してください。')
        initial = self._build_initial(community, participation)
        form = VketApplyForm(
            collaboration=collaboration,
            community=community,
            participation=participation,
            permissions=permissions,
            initial=initial,
        )
        return render(
            request,
            self.template_name,
            {
                'collaboration': collaboration,
                'community': community,
                'participation': participation,
                'form': form,
                'permissions': permissions,
            },
        )

    def post(self, request, pk: int):
        collaboration = get_object_or_404(VketCollaboration, pk=pk)
        community, membership = _get_active_membership(request)
        if community is None or membership is None:
            return HttpResponseForbidden('集会が選択されていません。ヘッダーの「マイ集会」から集会を選択してください。')

        if not (request.user.is_superuser or membership.role == CommunityMember.Role.OWNER):
            return HttpResponseForbidden('主催者のみ参加登録できます。')

        participation = (
            VketParticipation.objects.filter(collaboration=collaboration, community=community)
            .select_related('event')
            .first()
        )
        permissions = _apply_permissions_for_user(request.user, collaboration)
        if participation is None and not permissions.can_edit_schedule:
            return HttpResponseForbidden('受付期間外のため、新規の参加登録はできません。')
        if participation is not None and participation.event_id is None and not permissions.can_edit_schedule:
            return HttpResponseForbidden('日程が未確定のため、受付期間外の編集はできません。運営に日程登録を依頼してください。')
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
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    'collaboration': collaboration,
                    'community': community,
                    'participation': participation,
                    'form': form,
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
                )
        except IntegrityError:
            form.add_error(None, '保存に失敗しました（重複するイベントがある可能性があります）。時間を変更して再度お試しください。')
            return render(
                request,
                self.template_name,
                {
                    'collaboration': collaboration,
                    'community': community,
                    'participation': participation,
                    'form': form,
                    'permissions': permissions,
                },
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
                    'permissions': permissions,
                },
            )

        messages.success(request, '参加登録を保存しました。')
        return redirect('vket:apply', pk=collaboration.pk)

    def _build_initial(self, community: Community, participation: VketParticipation | None) -> dict:
        initial = {
            'start_time': community.start_time,
            'duration': community.duration,
        }

        if participation and participation.event_id:
            initial.update(
                {
                    'participation_date': participation.event.date,
                    'start_time': participation.event.start_time,
                    'duration': participation.event.duration,
                }
            )
            lt_detail = (
                participation.event.details.filter(detail_type='LT')
                .order_by('start_time', 'id')
                .first()
            )
            if lt_detail:
                initial.update({'speaker': lt_detail.speaker, 'theme': lt_detail.theme})
        if participation:
            initial['note'] = participation.note
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
    ) -> VketParticipation:
        if existing_participation:
            participation = existing_participation
        else:
            participation = VketParticipation(collaboration=collaboration, community=community)

        if permissions.can_edit_schedule:
            participation_date = cleaned['participation_date']
            start_time = cleaned['start_time']
            duration = cleaned['duration']

            if participation.event_id:
                event = participation.event
                old_start = event.start_time
                event.date = participation_date
                event.start_time = start_time
                event.duration = duration
                event.weekday = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][event.date.weekday()]
                event.save()

                if old_start != start_time:
                    delta = datetime.combine(event.date, start_time) - datetime.combine(event.date, old_start)
                    details = list(event.details.all())
                    for detail in details:
                        detail.start_time = _shift_time(detail.start_time, delta)
                    EventDetail.objects.bulk_update(details, ['start_time'])
            else:
                weekday_code = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][participation_date.weekday()]
                event = Event.objects.create(
                    community=community,
                    date=participation_date,
                    start_time=start_time,
                    duration=duration,
                    weekday=weekday_code,
                )
                participation.event = event

        if permissions.can_edit_lt:
            participation.note = cleaned.get('note', '')

            event = participation.event if participation.event_id else None
            speaker = cleaned.get('speaker', '').strip()
            theme = cleaned.get('theme', '').strip()
            if (speaker or theme) and event is None:
                raise ValueError('日程が未確定のため、LT情報（登壇者・テーマ）は保存できません。運営に日程登録を依頼してください。')
            if event and (speaker or theme or event.details.filter(detail_type='LT').exists()):
                lt_detail = (
                    event.details.filter(detail_type='LT')
                    .order_by('start_time', 'id')
                    .first()
                )
                if lt_detail is None:
                    lt_detail = EventDetail(event=event, detail_type='LT', status='approved', applicant=request.user)

                lt_detail.start_time = event.start_time
                lt_detail.duration = event.duration
                lt_detail.speaker = speaker
                lt_detail.theme = theme
                lt_detail.status = 'approved'
                if lt_detail.applicant_id is None:
                    lt_detail.applicant = request.user
                lt_detail.save()

        participation.save()
        return participation


class ManageView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'vket/manage.html'

    def test_func(self):
        return self.request.user.is_superuser

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        collaboration = get_object_or_404(VketCollaboration, pk=kwargs['pk'])

        lt_details_qs = (
            EventDetail.objects.filter(detail_type='LT', status='approved')
            .only('id', 'event_id', 'speaker', 'theme', 'start_time', 'duration', 'status', 'detail_type')
            .order_by('start_time', 'id')
        )

        participations = (
            VketParticipation.objects.filter(collaboration=collaboration)
            .select_related('community', 'event')
            .prefetch_related(Prefetch('event__details', queryset=lt_details_qs))
            .annotate(
                sort_has_event=Case(When(event__isnull=False, then=0), default=1, output_field=IntegerField())
            )
            .order_by('sort_has_event', 'event__date', 'event__start_time', 'community__name')
        )

        registered_community_ids = set(p.community_id for p in participations)
        all_communities = Community.objects.filter(status='approved').order_by('name')
        unregistered_communities = all_communities.exclude(id__in=registered_community_ids)

        scheduled_participations = [p for p in participations if p.event_id]
        lt_registered_count = 0
        for p in scheduled_participations:
            if any((d.speaker or d.theme) for d in p.event.details.all()):
                lt_registered_count += 1

        context.update(
            {
                'collaboration': collaboration,
                'participations': participations,
                'unregistered_communities': unregistered_communities,
                'community_total': all_communities.count(),
                'participation_count': len(participations),
                'scheduled_count': len(scheduled_participations),
                'lt_registered_count': lt_registered_count,
            }
        )
        return context


class ManageParticipationUpdateView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        return self.request.user.is_superuser

    def post(self, request, pk: int, participation_id: int):
        collaboration = get_object_or_404(VketCollaboration, pk=pk)
        participation = get_object_or_404(
            VketParticipation.objects.select_related('event', 'community'),
            pk=participation_id,
            collaboration=collaboration,
        )

        form = VketManageEventEditForm(
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

        event = participation.event
        old_start = event.start_time
        new_date = form.cleaned_data['date']
        new_start = form.cleaned_data['start_time']

        try:
            with transaction.atomic():
                event.date = new_date
                event.start_time = new_start
                event.weekday = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][new_date.weekday()]
                event.save()

                participation.admin_note = form.cleaned_data.get('admin_note', '')
                participation.save(update_fields=['admin_note', 'updated_at'])

                if old_start != new_start:
                    delta = datetime.combine(new_date, new_start) - datetime.combine(new_date, old_start)
                    details = list(event.details.all())
                    for detail in details:
                        detail.start_time = _shift_time(detail.start_time, delta)
                    EventDetail.objects.bulk_update(details, ['start_time'])

        except IntegrityError:
            messages.error(request, '保存に失敗しました（重複するイベントがある可能性があります）。')
            return redirect('vket:manage', pk=collaboration.pk)
        except ValueError as e:
            messages.error(request, str(e))
            return redirect('vket:manage', pk=collaboration.pk)

        messages.success(request, '日付・時刻を更新しました。')
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

        lt_details_qs = (
            EventDetail.objects.filter(detail_type='LT', status='approved')
            .only('id', 'event_id', 'start_time', 'duration', 'speaker', 'theme', 'status', 'detail_type')
            .order_by('start_time', 'id')
        )

        participations = (
            VketParticipation.objects.filter(collaboration=collaboration, event__isnull=False)
            .select_related('community', 'event')
            .prefetch_related(Prefetch('event__details', queryset=lt_details_qs))
            .order_by('event__date', 'event__start_time', 'community__name')
        )

        if not participations.exists():
            context.update(
                {
                    'collaboration': collaboration,
                    'slots': [],
                    'rows': [],
                    'overlap_warnings': [],
                    'warnings': [],
                }
            )
            return context

        # Slot range (30-min grid) is determined from all scheduled events.
        slot_minutes = 30

        def to_minutes(t: time) -> int:
            return t.hour * 60 + t.minute

        day_minutes = 24 * 60
        min_start = None
        max_end = None
        warnings = []

        lt_time_by_event_id: dict[int, time] = {}

        for p in participations:
            event = p.event

            start_min = to_minutes(event.start_time)
            end_min = start_min + event.duration
            if end_min > day_minutes:
                warnings.append(
                    f'{event.date.strftime("%Y/%m/%d")} {p.community.name} の開催時間が日跨ぎのため、0:00で切り捨て表示します'
                )
                end_min = day_minutes

            lt_details = list(event.details.all())
            lt_time = lt_details[0].start_time if lt_details else event.start_time
            lt_time_by_event_id[event.id] = lt_time

            lt_min = to_minutes(lt_time)
            lt_end_min = min(lt_min + slot_minutes, day_minutes)

            candidate_min = min(start_min, lt_min)
            candidate_max = max(end_min, lt_end_min)
            min_start = candidate_min if min_start is None else min(min_start, candidate_min)
            max_end = candidate_max if max_end is None else max(max_end, candidate_max)

        if min_start is None or max_end is None:
            context.update(
                {
                    'collaboration': collaboration,
                    'slots': [],
                    'rows': [],
                    'overlap_warnings': [],
                    'warnings': warnings,
                }
            )
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

        # Overlap map per date/slot index
        occupancy: dict[tuple, int] = {}
        for p in participations:
            event = p.event
            start_dt = datetime.combine(base, event.start_time)
            end_dt = start_dt + timedelta(minutes=event.duration)
            for idx, slot in enumerate(slots):
                slot_start = datetime.combine(base, slot.start)
                slot_end = datetime.combine(base, slot.end)
                if slot_start < end_dt and slot_end > start_dt:
                    key = (event.date, idx)
                    occupancy[key] = occupancy.get(key, 0) + 1

        rows = []
        overlap_warnings = []

        lt_slot_counts: dict[tuple, int] = {}
        lt_slot_names: dict[tuple, list[str]] = {}
        lt_index_by_event_id: dict[int, int] = {}

        for p in participations:
            event = p.event
            lt_time = lt_time_by_event_id[event.id]

            idx = slot_index_for(lt_time)
            if idx is None:
                warnings.append(
                    f'{event.date.strftime("%Y/%m/%d")} {p.community.name} のLT開始時刻（{lt_time.strftime("%H:%M")}）が表示範囲外です'
                )
                continue

            lt_index_by_event_id[event.id] = idx
            key = (event.date, idx)
            lt_slot_counts[key] = lt_slot_counts.get(key, 0) + 1
            lt_slot_names.setdefault(key, []).append(p.community.name)

            event_start_dt = datetime.combine(base, event.start_time)
            event_end_dt = event_start_dt + timedelta(minutes=event.duration)
            lt_dt = datetime.combine(base, lt_time)
            if not (event_start_dt <= lt_dt < event_end_dt):
                warnings.append(
                    f'{event.date.strftime("%Y/%m/%d")} {p.community.name} のLT開始時刻（{lt_time.strftime("%H:%M")}）が開催時間（{event.start_time.strftime("%H:%M")}〜{event.end_time.strftime("%H:%M")}）の範囲外です'
                )

        # Pairwise warnings
        for d, group in groupby(participations, key=lambda p: p.event.date):
            events_on_date = list(group)
            for p1, p2 in combinations(events_on_date, 2):
                e1, e2 = p1.event, p2.event
                if _time_ranges_overlap(e1.start_time, e1.duration, e2.start_time, e2.duration):
                    overlap_start = max(e1.start_time, e2.start_time)
                    overlap_warnings.append(
                        f'{d.strftime("%Y/%m/%d")} {overlap_start.strftime("%H:%M")} {p1.community.name} と {p2.community.name} が重複'
                    )

        for (d, idx), names in sorted(lt_slot_names.items(), key=lambda x: (x[0][0], x[0][1])):
            if len(names) <= 1:
                continue
            warnings.append(
                f'{d.strftime("%Y/%m/%d")} {slots[idx].start.strftime("%H:%M")} LT開始が重複: {", ".join(sorted(names))}'
            )

        for p in participations:
            event = p.event
            base_date = timezone.localdate()
            event_start = datetime.combine(base_date, event.start_time)
            event_end = event_start + timedelta(minutes=event.duration)
            lt_time = lt_time_by_event_id.get(event.id)
            lt_idx = lt_index_by_event_id.get(event.id)
            cells = []
            for idx, slot in enumerate(slots):
                slot_start = datetime.combine(base_date, slot.start)
                slot_end = datetime.combine(base_date, slot.end)
                occupied = slot_start < event_end and slot_end > event_start
                overlap = occupied and occupancy.get((event.date, idx), 0) > 1
                lt = lt_idx == idx if lt_idx is not None else False
                lt_overlap = lt and lt_slot_counts.get((event.date, idx), 0) > 1
                cells.append({'occupied': occupied, 'overlap': overlap, 'lt': lt, 'lt_overlap': lt_overlap})
            rows.append({'participation': p, 'event': event, 'cells': cells, 'lt_time': lt_time})

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
