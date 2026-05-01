from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Prefetch
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from event.models import EventDetail

from ..models import (
    VketCollaboration,
    VketParticipation,
    VketPresentation,
)
from .helpers import (
    _build_schedule_context,
    _get_active_membership,
    _get_visible_collaborations,
    _is_vket_admin,
)

VKET_STAGE_CREATE_URL = 'https://vket.com/hub/2026Summer/notification'


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

        if not (request.user.is_superuser or membership):
            return HttpResponseForbidden('集会メンバーのみステージ登録を完了できます。')

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

        # 日程表データ（承認済みメンバーなら閲覧可能）
        schedule_ctx = {}
        if membership:
            schedule_ctx = _build_schedule_context(collaboration, include_requested=True)

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
                'is_admin': _is_vket_admin(request.user),
                'stage_url': VKET_STAGE_CREATE_URL,
                'event_details': event_details,
                **schedule_ctx,
            },
        )
