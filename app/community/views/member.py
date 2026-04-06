"""メンバー管理系ビュー: 集会切り替え、メンバー管理、招待."""
import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import TemplateView

from ..models import Community, CommunityMember, CommunityInvitation

logger = logging.getLogger(__name__)


class SwitchCommunityView(LoginRequiredMixin, View):
    """アクティブな集会を切り替えるビュー"""

    def _is_safe_redirect_url(self, request, url: str) -> bool:
        if not url:
            return False
        return url_has_allowed_host_and_scheme(
            url=url,
            allowed_hosts={request.get_host()},
            require_https=False,
        )

    def _get_redirect_url(self, request, success=False):
        """リダイレクト先URLを取得する。

        POSTパラメータのredirect_toを優先し、なければHTTP_REFERERを使用。
        両方ない場合はevent:my_listにリダイレクト。

        Args:
            request: HTTPリクエスト
            success: 切り替え成功時かどうか（True: redirect_to優先、False: referer優先）
        """
        redirect_to = request.POST.get('redirect_to')
        referer = request.META.get('HTTP_REFERER', '')

        if success and redirect_to and self._is_safe_redirect_url(request, redirect_to):
            return redirect_to
        if referer and self._is_safe_redirect_url(request, referer):
            return referer
        return 'event:my_list'

    def post(self, request):
        community_id = request.POST.get('community_id')

        if community_id:
            # community_idが整数に変換可能かを先にチェック
            try:
                community_id_int = int(community_id)
            except (ValueError, TypeError):
                messages.error(request, '無効な集会IDです。')
                return redirect(self._get_redirect_url(request, success=False))

            # ユーザーがその集会のメンバーであることを確認
            membership = request.user.community_memberships.select_related(
                'community'
            ).filter(community_id=community_id_int).first()

            if membership:
                if membership.community.is_ended:
                    messages.error(request, 'この集会は終了しています。')
                    return redirect(self._get_redirect_url(request, success=False))
                request.session['active_community_id'] = community_id_int
                messages.success(request, '集会を切り替えました。')
                # 切り替え成功時はredirect_toまたはevent:my_listに遷移
                return redirect(self._get_redirect_url(request, success=True))
            else:
                messages.error(request, 'この集会へのアクセス権限がありません。')
        else:
            messages.error(request, '集会が指定されていません。')

        return redirect(self._get_redirect_url(request, success=False))


class CommunityMemberManageView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """集会メンバー管理ビュー（主催者のみ）"""
    template_name = 'community/member_manage.html'

    def test_func(self):
        community = get_object_or_404(Community, pk=self.kwargs['pk'])
        return community.is_owner(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        community = get_object_or_404(Community, pk=self.kwargs['pk'])
        context['community'] = community
        context['members'] = community.members.select_related('user').order_by('role', 'created_at')
        # 有効な招待リンク一覧を取得
        context['invitations'] = community.invitations.filter(
            expires_at__gt=timezone.now()
        ).select_related('created_by').order_by('-created_at')
        return context


class RemoveStaffView(LoginRequiredMixin, View):
    """スタッフ削除ビュー"""

    def post(self, request, pk, member_id):
        community = get_object_or_404(Community, pk=pk)
        if not community.is_owner(request.user):
            messages.error(request, '権限がありません')
            return redirect('community:member_manage', pk=pk)

        member = get_object_or_404(CommunityMember, pk=member_id, community=community)

        # 主催者自身は削除不可
        if member.is_owner and member.user == request.user:
            messages.error(request, '自分自身を削除することはできません')
            return redirect('community:member_manage', pk=pk)

        # 最後の主催者は削除不可
        if member.is_owner and community.members.filter(role=CommunityMember.Role.OWNER).count() <= 1:
            messages.error(request, '最後の主催者は削除できません')
            return redirect('community:member_manage', pk=pk)

        user_name = member.user.user_name
        member.delete()
        messages.success(request, f'{user_name} をメンバーから削除しました')

        return redirect('community:member_manage', pk=pk)


class CreateInvitationView(LoginRequiredMixin, View):
    """招待リンク生成ビュー（主催者のみ）"""

    def post(self, request, pk):
        community = get_object_or_404(Community, pk=pk)
        if not community.is_owner(request.user):
            messages.error(request, '権限がありません')
            return redirect('community:member_manage', pk=pk)

        CommunityInvitation.create_invitation(community, request.user)
        messages.success(request, '招待リンクを生成しました')

        return redirect('community:member_manage', pk=pk)


class RevokeInvitationView(LoginRequiredMixin, View):
    """招待リンク削除ビュー（主催者のみ）"""

    def post(self, request, pk, invitation_id):
        community = get_object_or_404(Community, pk=pk)
        if not community.is_owner(request.user):
            messages.error(request, '権限がありません')
            return redirect('community:member_manage', pk=pk)

        invitation = get_object_or_404(CommunityInvitation, pk=invitation_id, community=community)
        invitation.delete()
        messages.success(request, '招待リンクを削除しました')

        return redirect('community:member_manage', pk=pk)


class AcceptInvitationView(View):
    """招待リンク受け入れビュー"""

    def get(self, request, token):
        """招待確認画面を表示"""
        invitation = get_object_or_404(CommunityInvitation, token=token)

        # 有効期限チェック
        if not invitation.is_valid:
            messages.error(request, 'この招待リンクは有効期限が切れています')
            return redirect('ta_hub:index')

        context = {
            'invitation': invitation,
            'community': invitation.community,
        }

        # 既存メンバーチェック（ログイン済みの場合）
        if request.user.is_authenticated:
            if invitation.community.is_manager(request.user):
                context['is_already_member'] = True

        return render(request, 'community/accept_invitation.html', context)

    def post(self, request, token):
        """招待を受け入れてメンバーになる"""
        # ログインが必要
        if not request.user.is_authenticated:
            messages.error(request, '招待を受けるにはログインが必要です')
            # ログイン後にこのページに戻ってくるようにnextパラメータを設定
            return redirect(f'/accounts/login/?next={request.path}')

        invitation = get_object_or_404(CommunityInvitation, token=token)

        # 有効期限チェック
        if not invitation.is_valid:
            messages.error(request, 'この招待リンクは有効期限が切れています')
            return redirect('ta_hub:index')

        # 既存メンバーチェック
        if invitation.community.is_manager(request.user):
            messages.info(request, 'あなたは既にこの集会のメンバーです')
            return redirect('community:detail', pk=invitation.community.pk)

        # メンバーとして追加
        CommunityMember.objects.create(
            community=invitation.community,
            user=request.user,
            role=CommunityMember.Role.STAFF
        )

        messages.success(request, f'{invitation.community.name} のスタッフになりました')
        return redirect('community:detail', pk=invitation.community.pk)
