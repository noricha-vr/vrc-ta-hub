from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.views import View

from event.models import Event, EventDetail
from ta_hub.index_cache import clear_index_view_cache

from ..models import (
    VketCollaboration,
    VketParticipation,
    VketPresentation,
)
from .helpers import _is_vket_admin

logger = logging.getLogger(__name__)


class ManagePublishView(LoginRequiredMixin, UserPassesTestMixin, View):
    """運営向け: LOCKEDフェーズのコラボをEventとして公開するビュー"""

    def test_func(self):
        return _is_vket_admin(self.request.user)

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

        if published_count:
            clear_index_view_cache()

        messages.success(
            request,
            f'公開処理完了: {published_count}件のイベントを公開しました。',
        )
        return redirect('vket:manage', pk=collaboration.pk)
