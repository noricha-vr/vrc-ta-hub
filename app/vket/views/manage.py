from __future__ import annotations

import logging
import re
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import transaction
from django.db.models import Case, IntegerField, Prefetch, When
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from allauth.socialaccount.models import SocialAccount

from community.models import Community
from event.models import EventDetail
from ta_hub.index_cache import clear_index_view_cache

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


class ManageView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
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


class ManageParticipationUpdateView(LoginRequiredMixin, UserPassesTestMixin, View):
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
                changed_publication = self._clear_published_event(participation)
                participation.save(
                    update_fields=[
                        'lifecycle',
                        'admin_note',
                        'published_event',
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

        # DRAFT のLTを一括確定
        participation.presentations.filter(
            status=VketPresentation.Status.DRAFT,
        ).update(status=VketPresentation.Status.CONFIRMED)

        # published_event がある場合、EventDetail 未作成の CONFIRMED LT に EventDetail を作成
        changed_index_detail = False
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
                changed_index_detail = True

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
                    updated_count = EventDetail.objects.filter(pk=detail_id).update(start_time=new_time)
                    changed_index_detail = changed_index_detail or updated_count > 0
                except (ValueError, KeyError):
                    logger.warning('EventDetail #%d の start_time パース失敗: %s', detail_id, time_str)

        if changed_index_detail:
            clear_index_view_cache()

        messages.success(
            request,
            f'{participation.community.name} の日程を確定しました。',
        )
        return redirect('vket:manage', pk=collaboration.pk)

    @staticmethod
    def _clear_published_event(participation: VketParticipation) -> bool:
        if not participation.published_event_id:
            return False

        published_event = participation.published_event
        detail_ids = list(
            participation.presentations.filter(
                published_event_detail__isnull=False,
            ).values_list('published_event_detail_id', flat=True)
        )
        participation.presentations.filter(
            published_event_detail__isnull=False,
        ).update(published_event_detail=None)

        if detail_ids:
            EventDetail.objects.filter(pk__in=detail_ids).delete()

        participation.published_event = None
        if not published_event.vket_participations.exclude(pk=participation.pk).exists():
            published_event.delete()
        return True


class ManageScheduleView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'vket/manage_schedule.html'

    def test_func(self):
        return _is_vket_admin(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        collaboration = get_object_or_404(VketCollaboration, pk=kwargs['pk'])
        schedule_ctx = _build_schedule_context(collaboration, include_requested=True)
        context.update({'collaboration': collaboration, **schedule_ctx})
        return context
