from __future__ import annotations

from datetime import date

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
VKET_STAGE_CREATE_URL_FALLBACK_SLUGS = {'vket-2026-summer'}
VKET_STAGE_REGISTRATION_GUIDANCE_BY_SLUG = {
    'vket-2026-summer': {
        'venue': 'Parareal Central Ignition Point - 着火点 - - エントランス',
        'tag': 'Vketステージ',
    },
}


def _resolve_stage_url(collaboration: VketCollaboration) -> str:
    settings_json = collaboration.settings_json or {}
    if isinstance(settings_json, dict):
        stage_url = settings_json.get('stage_url')
        if isinstance(stage_url, str) and stage_url.strip():
            return stage_url.strip()

    if collaboration.slug in VKET_STAGE_CREATE_URL_FALLBACK_SLUGS:
        return VKET_STAGE_CREATE_URL
    return ''


def _resolve_stage_registration_guidance(collaboration: VketCollaboration) -> dict[str, str]:
    return VKET_STAGE_REGISTRATION_GUIDANCE_BY_SLUG.get(collaboration.slug, {})


def _is_stage_register_open(
    participation: VketParticipation | None,
    collaboration: VketCollaboration,
    current_date: date | None = None,
) -> bool:
    if participation is None:
        return False
    if participation.stage_registered_at:
        return False
    if participation.progress == VketParticipation.Progress.NOT_APPLIED:
        return False
    if participation.lifecycle != VketParticipation.Lifecycle.ACTIVE:
        return False

    current_date = current_date or timezone.localdate()
    return current_date <= collaboration.period_end


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

        if participation.progress == VketParticipation.Progress.NOT_APPLIED:
            messages.warning(request, 'ステージ登録は参加申込み後に行ってください。')
            return redirect('vket:status', pk=pk)
        if participation.stage_registered_at:
            messages.warning(request, 'ステージ登録は既に完了しています。')
            return redirect('vket:status', pk=pk)
        if timezone.localdate() > collaboration.period_end:
            messages.warning(request, '開催期間終了後はステージ登録を記録できません。')
            return redirect('vket:status', pk=pk)
        if not _is_stage_register_open(participation, collaboration):
            messages.warning(request, '参加中の集会のみステージ登録を記録できます。')
            return redirect('vket:status', pk=pk)

        participation.stage_registered_at = timezone.now()
        update_fields = ['stage_registered_at', 'updated_at']
        if participation.progress == VketParticipation.Progress.APPLIED:
            participation.progress = VketParticipation.Progress.STAGE_REGISTERED
            update_fields.append('progress')
        # 運営処理で進んだ進捗は現在の工程を示すため、登録記録時に巻き戻さない。
        # APPLIED の参加だけ従来どおり STAGE_REGISTERED へ前進させる。
        participation.save(update_fields=update_fields)

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
                'stage_url': _resolve_stage_url(collaboration),
                'stage_registration_guidance': _resolve_stage_registration_guidance(collaboration),
                'stage_register_open': _is_stage_register_open(participation, collaboration),
                'event_details': event_details,
                **schedule_ctx,
            },
        )
