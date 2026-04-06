from __future__ import annotations

from itertools import groupby

from django.db.models import Case, IntegerField, Prefetch, When
from django.shortcuts import get_object_or_404
from django.views.generic import DetailView, ListView

from event.models import EventDetail

from ..models import VketCollaboration, VketParticipation
from .common import (
    PHASE_SORT_ORDER,
    _apply_permissions_for_user,
    _get_active_membership,
    _is_vket_admin,
)


class CollaborationListView(ListView):
    model = VketCollaboration
    template_name = 'vket/collaboration_list.html'
    context_object_name = 'collaborations'

    def get_queryset(self):
        qs = super().get_queryset()
        # 管理者以外は下書きを除外
        if not _is_vket_admin(self.request.user):
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_admin'] = _is_vket_admin(self.request.user)
        return context


class CollaborationDetailView(DetailView):
    model = VketCollaboration
    template_name = 'vket/collaboration_detail.html'
    context_object_name = 'collaboration'

    def get_queryset(self):
        qs = super().get_queryset()
        # 管理者以外は下書きを除外
        if not _is_vket_admin(self.request.user):
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
        is_member = bool(membership)
        participation_exists = False
        participation_has_event = False
        if is_member and community:
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
            is_member
            and (
                permissions.can_edit_schedule
                or (permissions.can_edit_lt and participation_exists)
            )
        )
        context['is_admin'] = _is_vket_admin(self.request.user)
        context['participation_exists'] = participation_exists
        context['participation_has_event'] = participation_has_event
        return context
