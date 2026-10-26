"""ステージ登録用のコピー画面とサムネイル。"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from community.models import CommunityMember

from ..models import VketParticipation
from ..stage_copy import build_stage_copy
from ..stage_thumbnail import MAX_THUMBNAIL_ATTEMPTS, generate_stage_thumbnail, log_thumbnail
from .helpers import _get_active_membership, _get_visible_collaborations, _is_vket_admin
from .status import _resolve_stage_registration_guidance, _resolve_stage_url, _stage_registration_enabled


class StageParticipationMixin(LoginRequiredMixin):
    def get_participation(self, request, pk, participation_id):
        participation = get_object_or_404(
            VketParticipation.objects.select_related('community', 'collaboration'),
            pk=participation_id, collaboration__in=_get_visible_collaborations(request.user), collaboration_id=pk,
        )
        if not _is_vket_admin(request.user):
            _, membership = _get_active_membership(request)
            if membership is None or membership.community_id != participation.community_id:
                membership = request.user.community_memberships.filter(community_id=participation.community_id).first()
            if not membership or membership.role not in {CommunityMember.Role.OWNER, CommunityMember.Role.STAFF}:
                raise PermissionDenied
        return participation


class StageCopyView(StageParticipationMixin, View):
    def get(self, request, pk, participation_id):
        participation = self.get_participation(request, pk, participation_id)
        collaboration = participation.collaboration
        log_thumbnail(participation, 'viewed')
        return render(request, 'vket/stage_copy.html', {
            'participation': participation,
            'community': participation.community,
            'collaboration': collaboration,
            'stage_url': _resolve_stage_url(collaboration),
            'stage_registration_enabled': _stage_registration_enabled(collaboration),
            'max_thumbnail_attempts': MAX_THUMBNAIL_ATTEMPTS,
            **build_stage_copy(participation, _resolve_stage_registration_guidance(collaboration)),
        })


class StageThumbnailGenerateView(StageParticipationMixin, View):
    def post(self, request, pk, participation_id):
        participation = self.get_participation(request, pk, participation_id)
        if not _stage_registration_enabled(participation.collaboration):
            messages.warning(request, 'ステージ登録の受付開始後に生成できます。')
        else:
            result = generate_stage_thumbnail(participation)
            if result == 'generated':
                messages.success(request, 'サムネイルを生成しました。文字の誤字を確かめてからお使いください。')
            elif result == 'failed':
                messages.error(request, 'サムネイルを生成できませんでした。時間をおいて再試行してください。')
            elif result == 'attempts_exhausted':
                messages.error(request, '生成に3回失敗したため、これ以上は生成できません。運営にお問い合わせください。')
            elif result == 'no_poster':
                messages.warning(request, '先に集会のポスターを登録してください。')
            else:
                messages.info(request, 'サムネイルは生成済み、または生成中です。しばらくしてから再読み込みしてください。')
        # 結果の画像とメッセージがすぐ見えるよう、サムネイルの欄へ戻す
        url = reverse('vket:stage_copy', kwargs={'pk': pk, 'participation_id': participation_id})
        return redirect(f'{url}#stage-thumbnail')


class StageThumbnailDownloadView(StageParticipationMixin, View):
    def get(self, request, pk, participation_id):
        participation = self.get_participation(request, pk, participation_id)
        if not participation.stage_thumbnail or not participation.stage_thumbnail_generated_at:
            raise Http404
        return FileResponse(
            participation.stage_thumbnail.open('rb'), as_attachment=True,
            filename=f'stage-{participation.pk}.jpg', content_type='image/jpeg',
        )
