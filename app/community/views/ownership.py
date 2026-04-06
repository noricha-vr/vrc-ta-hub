from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from community.models import (
    INVITATION_EXPIRATION_DAYS,
    Community,
    CommunityInvitation,
    CommunityMember,
)

import community.views as community_views


class CreateOwnershipTransferView(LoginRequiredMixin, View):
    """主催者引き継ぎリンク生成ビュー（主催者のみ）"""

    def post(self, request, pk):
        community = get_object_or_404(Community, pk=pk)

        if not community.is_owner(request.user):
            messages.error(request, '権限がありません')
            return redirect('community:settings')

        existing = CommunityInvitation.objects.filter(
            community=community,
            invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER,
            expires_at__gt=timezone.now(),
        ).first()

        if existing:
            messages.warning(request, '有効な引き継ぎリンクが既に存在します')
            return redirect('community:settings')

        CommunityInvitation.objects.create(
            community=community,
            created_by=request.user,
            invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER,
            expires_at=timezone.now() + timedelta(days=INVITATION_EXPIRATION_DAYS),
        )

        messages.success(request, '引き継ぎリンクを生成しました')
        community_views.logger.info(
            f'主催者引き継ぎリンク生成: 集会「{community.name}」、作成者: {request.user.user_name}'
        )

        return redirect('community:settings')


class AcceptOwnershipTransferView(View):
    """主催者引き継ぎ受け入れビュー"""

    def get(self, request, token):
        invitation = get_object_or_404(
            CommunityInvitation,
            token=token,
            invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER,
        )

        if not invitation.is_valid:
            messages.error(request, 'この引き継ぎリンクは有効期限が切れています')
            return redirect('ta_hub:index')

        context = {
            'invitation': invitation,
            'community': invitation.community,
        }

        if request.user.is_authenticated and invitation.community.is_owner(request.user):
            context['is_current_owner'] = True

        return render(request, 'community/accept_ownership_transfer.html', context)

    def post(self, request, token):
        if not request.user.is_authenticated:
            messages.error(request, '引き継ぎを受けるにはログインが必要です')
            return redirect(f'/accounts/login/?next={request.path}')

        invitation = get_object_or_404(
            CommunityInvitation,
            token=token,
            invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER,
        )

        if not invitation.is_valid:
            messages.error(request, 'この引き継ぎリンクは有効期限が切れています')
            return redirect('ta_hub:index')

        community = invitation.community

        if community.is_owner(request.user):
            messages.error(request, 'あなたは既にこの集会の主催者です')
            return redirect('community:detail', pk=community.pk)

        with transaction.atomic():
            current_owners = CommunityMember.objects.filter(
                community=community,
                role=CommunityMember.Role.OWNER,
            )
            for owner_member in current_owners:
                owner_member.role = CommunityMember.Role.STAFF
                owner_member.save()
                community_views.logger.info(
                    f'主催者降格: {owner_member.user.user_name} → スタッフ（集会: {community.name}）'
                )

            new_owner_member, created = CommunityMember.objects.get_or_create(
                community=community,
                user=request.user,
                defaults={'role': CommunityMember.Role.OWNER},
            )
            if not created:
                new_owner_member.role = CommunityMember.Role.OWNER
                new_owner_member.save()
                community_views.logger.info(
                    f'スタッフ昇格: {request.user.user_name} → 主催者（集会: {community.name}）'
                )
            else:
                community_views.logger.info(
                    f'新規主催者追加: {request.user.user_name}（集会: {community.name}）'
                )

            invitation.delete()

        messages.success(request, f'{community.name} の主催者を引き継ぎました')
        return redirect('community:detail', pk=community.pk)


class RevokeOwnershipTransferView(LoginRequiredMixin, View):
    """引き継ぎリンク削除ビュー（主催者のみ）"""

    def post(self, request, pk, invitation_id):
        community = get_object_or_404(Community, pk=pk)

        if not community.is_owner(request.user):
            messages.error(request, '権限がありません')
            return redirect('community:settings')

        invitation = get_object_or_404(
            CommunityInvitation,
            pk=invitation_id,
            community=community,
            invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER,
        )
        invitation.delete()

        messages.success(request, '引き継ぎリンクを削除しました')
        community_views.logger.info(
            f'主催者引き継ぎリンク削除: 集会「{community.name}」、削除者: {request.user.user_name}'
        )

        return redirect('community:settings')
