from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.views import View

from ta_hub.access_mixins import AuthenticatedForbiddenMixin
from ta_hub.index_cache import clear_index_view_cache
from vket.services import sync_participation_publication

from ..models import (
    VketCollaboration,
    VketParticipation,
)
from ..schedule import ALLOW_OVERLAP_FIELD, find_publish_target_conflicts, format_pair
from .helpers import _is_vket_admin

logger = logging.getLogger(__name__)


class ManagePublishView(LoginRequiredMixin, AuthenticatedForbiddenMixin, View):
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

        # 「確定」の関門はここだけ。重なりは既定で止め、承知のフラグがある時だけ通す
        overlapping_pairs = find_publish_target_conflicts(collaboration)
        pair_text = ' / '.join(format_pair(a, b) for a, b in overlapping_pairs)
        if overlapping_pairs and request.POST.get(ALLOW_OVERLAP_FIELD) != '1':
            messages.error(
                request,
                '時間が重なっている集会があるため、公開同期を実行しませんでした。'
                '重なりを解消するか、意図して同じ時間に行う場合は「重なりを承知で公開する」を'
                f'選んで、もう一度実行してください: {pair_text}',
            )
            return redirect('vket:manage', pk=collaboration.pk)
        if overlapping_pairs:
            logger.warning(
                'Vketコラボ公開: 重なりを承知で公開',
                extra={
                    'collaboration_id': collaboration.id,
                    'user_id': request.user.id,
                    'overlapping_participation_ids': [
                        [a.participation_id, b.participation_id] for a, b in overlapping_pairs
                    ],
                },
            )
            messages.warning(request, f'次の重なりを承知で公開しました: {pair_text}')

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

                sync_result = sync_participation_publication(participation)
                participation.progress = VketParticipation.Progress.DONE
                participation.save(update_fields=['progress', 'updated_at'])

                published_count += 1
                if sync_result.changed_index_data:
                    clear_index_view_cache()
                logger.info(
                    'Vketコラボイベント公開',
                    extra={
                        'collaboration_id': collaboration.id,
                        'participation_id': participation.id,
                        'community_name': participation.community.name,
                        'event_id': sync_result.event.id,
                    },
                )

        if published_count:
            clear_index_view_cache()

        messages.success(
            request,
            f'公開処理完了: {published_count}件のイベントを公開しました。',
        )
        return redirect('vket:manage', pk=collaboration.pk)
