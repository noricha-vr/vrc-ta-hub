from __future__ import annotations

import logging
import re
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Case, IntegerField, Prefetch, When
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from allauth.socialaccount.models import SocialAccount

from community.models import Community
from ta_hub.access_mixins import AuthenticatedForbiddenMixin
from ta_hub.index_cache import clear_index_view_cache
from vket.services import clear_participation_publication, sync_participation_publication

from ..forms import VketManageParticipationForm
from ..models import (
    VketCollaboration,
    VketParticipation,
    VketPresentation,
)
from .helpers import (
    _build_schedule_context,
    _is_vket_admin,
)

logger = logging.getLogger(__name__)


class ManageView(LoginRequiredMixin, AuthenticatedForbiddenMixin, TemplateView):
    template_name = 'vket/manage.html'

    def test_func(self):
        return _is_vket_admin(self.request.user)

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
        today = timezone.localdate()
        all_communities = Community.objects.filter(status='approved').exclude(
            end_at__lt=today,
        ).order_by('name')
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


class ManageParticipationUpdateView(LoginRequiredMixin, AuthenticatedForbiddenMixin, View):
    def test_func(self):
        return _is_vket_admin(self.request.user)

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

        participation.lifecycle = form.cleaned_data['lifecycle']
        participation.admin_note = form.cleaned_data.get('admin_note', '')

        if participation.lifecycle != VketParticipation.Lifecycle.ACTIVE:
            with transaction.atomic():
                changed_publication = clear_participation_publication(participation)
                participation.save(
                    update_fields=[
                        'lifecycle',
                        'admin_note',
                        'updated_at',
                    ]
                )
            if changed_publication:
                clear_index_view_cache()
            messages.success(
                request,
                f'{participation.community.name} の参加状態を更新しました。',
            )
            return redirect('vket:manage', pk=collaboration.pk)

        # 確定日程・運営備考の更新
        participation.confirmed_date = form.cleaned_data['confirmed_date']
        participation.confirmed_start_time = form.cleaned_data['confirmed_start_time']
        participation.confirmed_duration = form.cleaned_data['confirmed_duration']
        participation.schedule_adjusted_by_admin = True
        participation.progress = VketParticipation.Progress.REHEARSAL
        participation.schedule_confirmed_at = timezone.now()

        participation.save(
            update_fields=[
                'lifecycle',
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

        pres_pattern = re.compile(r'^pres_(\d+)_start_time$')
        pres_updates = {}
        for key, value in request.POST.items():
            m = pres_pattern.match(key)
            if m and value:
                pres_updates[int(m.group(1))] = value

        allowed_presentation_ids = set(
            participation.presentations.filter(
                pk__in=pres_updates.keys(),
                status=VketPresentation.Status.CONFIRMED,
            ).values_list('id', flat=True)
        )

        # DRAFT のLTを一括確定
        participation.presentations.filter(
            status=VketPresentation.Status.DRAFT,
        ).update(status=VketPresentation.Status.CONFIRMED)

        changed_index_detail = False

        # 発表ごとの確定開始時刻を更新する。EventDetail への反映は公開同期でまとめて行う。
        if pres_updates:
            allowed_presentations = {
                pres.pk: pres
                for pres in participation.presentations.select_related(
                    'published_event_detail'
                ).filter(
                    pk__in=allowed_presentation_ids,
                    status=VketPresentation.Status.CONFIRMED,
                )
            }

            for pres_id, time_str in pres_updates.items():
                pres = allowed_presentations.get(pres_id)
                if pres is None:
                    continue
                try:
                    new_time = datetime.strptime(time_str, '%H:%M').time()
                    pres.confirmed_start_time = new_time
                    pres.save(update_fields=['confirmed_start_time', 'updated_at'])
                except (ValueError, KeyError):
                    logger.warning('VketPresentation #%d の start_time パース失敗: %s', pres_id, time_str)

        with transaction.atomic():
            sync_result = sync_participation_publication(participation)
            changed_index_detail = sync_result.changed_index_data

        if changed_index_detail:
            clear_index_view_cache()

        messages.success(
            request,
            f'{participation.community.name} の日程を確定しました。',
        )
        return redirect('vket:manage', pk=collaboration.pk)


class ManageScheduleView(LoginRequiredMixin, AuthenticatedForbiddenMixin, TemplateView):
    template_name = 'vket/manage_schedule.html'

    def test_func(self):
        return _is_vket_admin(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        collaboration = get_object_or_404(VketCollaboration, pk=kwargs['pk'])
        schedule_ctx = _build_schedule_context(collaboration, include_requested=True)
        context.update({'collaboration': collaboration, **schedule_ctx})
        return context
