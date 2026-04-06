"""設定系ビュー: 集会設定、主催者引き継ぎ、Webhook、LT設定."""
import logging
from datetime import timedelta

import requests
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from ..models import Community, CommunityMember, CommunityInvitation, INVITATION_EXPIRATION_DAYS

logger = logging.getLogger(__name__)


class CommunitySettingsView(LoginRequiredMixin, TemplateView):
    """集会設定ページビュー"""
    template_name = "community/settings.html"

    def get_active_community(self):
        """セッションからアクティブな集会を取得"""
        community_id = self.request.session.get('active_community_id')
        if community_id:
            try:
                membership = self.request.user.community_memberships.select_related(
                    'community'
                ).get(community_id=community_id)
                return membership.community, membership
            except CommunityMember.DoesNotExist:
                pass

        # フォールバック: 最初のメンバーシップを取得
        membership = self.request.user.community_memberships.select_related(
            'community'
        ).first()
        if membership:
            return membership.community, membership

        return None, None

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            community, _ = self.get_active_community()
            if not community:
                messages.warning(request, '管理している集会がありません。')
                return redirect('account:settings')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        community, membership = self.get_active_community()
        context['community'] = community

        # is_owner の判定
        if membership:
            context['is_owner'] = membership.role == CommunityMember.Role.OWNER
        else:
            context['is_owner'] = False

        # 有効な引き継ぎリンクを取得
        if context['is_owner'] and community:
            context['ownership_transfer_invitation'] = CommunityInvitation.objects.filter(
                community=community,
                invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER,
                expires_at__gt=timezone.now()
            ).first()

        # LT申請テンプレートのデフォルト値を設定
        if community:
            context['lt_application_template_display'] = (
                community.lt_application_template or
                "【発表概要】\n\n【スライド公開】OK / NG\n\n【動画撮影】YouTube公開 / Discord限定 / OK / NG"
            )

        return context


class CreateOwnershipTransferView(LoginRequiredMixin, View):
    """主催者引き継ぎリンク生成ビュー（主催者のみ）"""

    def post(self, request, pk):
        community = get_object_or_404(Community, pk=pk)

        # 権限チェック（主催者のみ）
        if not community.is_owner(request.user):
            messages.error(request, '権限がありません')
            return redirect('community:settings')

        # 既存の有効な引き継ぎリンクがあるか確認
        existing = CommunityInvitation.objects.filter(
            community=community,
            invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER,
            expires_at__gt=timezone.now()
        ).first()

        if existing:
            messages.warning(request, '有効な引き継ぎリンクが既に存在します')
            return redirect('community:settings')

        # 引き継ぎリンクを作成
        CommunityInvitation.objects.create(
            community=community,
            created_by=request.user,
            invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER,
            expires_at=timezone.now() + timedelta(days=INVITATION_EXPIRATION_DAYS)
        )

        messages.success(request, '引き継ぎリンクを生成しました')
        logger.info(f'主催者引き継ぎリンク生成: 集会「{community.name}」、作成者: {request.user.user_name}')

        return redirect('community:settings')


class AcceptOwnershipTransferView(View):
    """主催者引き継ぎ受け入れビュー"""

    def get(self, request, token):
        """引き継ぎ確認画面を表示"""
        invitation = get_object_or_404(
            CommunityInvitation,
            token=token,
            invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER
        )

        # 有効期限チェック
        if not invitation.is_valid:
            messages.error(request, 'この引き継ぎリンクは有効期限が切れています')
            return redirect('ta_hub:index')

        context = {
            'invitation': invitation,
            'community': invitation.community,
        }

        # 自分自身への引き継ぎチェック（ログイン済みの場合）
        if request.user.is_authenticated:
            if invitation.community.is_owner(request.user):
                context['is_current_owner'] = True

        return render(request, 'community/accept_ownership_transfer.html', context)

    def post(self, request, token):
        """引き継ぎを実行"""
        # ログインが必要
        if not request.user.is_authenticated:
            messages.error(request, '引き継ぎを受けるにはログインが必要です')
            return redirect(f'/accounts/login/?next={request.path}')

        invitation = get_object_or_404(
            CommunityInvitation,
            token=token,
            invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER
        )

        # 有効期限チェック
        if not invitation.is_valid:
            messages.error(request, 'この引き継ぎリンクは有効期限が切れています')
            return redirect('ta_hub:index')

        community = invitation.community

        # 自分自身への引き継ぎチェック
        if community.is_owner(request.user):
            messages.error(request, 'あなたは既にこの集会の主催者です')
            return redirect('community:detail', pk=community.pk)

        # トランザクション内で引き継ぎを実行
        from django.db import transaction
        with transaction.atomic():
            # 現在の主催者をスタッフに降格
            current_owners = CommunityMember.objects.filter(
                community=community,
                role=CommunityMember.Role.OWNER
            )
            for owner_member in current_owners:
                owner_member.role = CommunityMember.Role.STAFF
                owner_member.save()
                logger.info(f'主催者降格: {owner_member.user.user_name} → スタッフ（集会: {community.name}）')

            # 新しい主催者を設定
            new_owner_member, created = CommunityMember.objects.get_or_create(
                community=community,
                user=request.user,
                defaults={'role': CommunityMember.Role.OWNER}
            )
            if not created:
                # 既存スタッフの場合は昇格
                new_owner_member.role = CommunityMember.Role.OWNER
                new_owner_member.save()
                logger.info(f'スタッフ昇格: {request.user.user_name} → 主催者（集会: {community.name}）')
            else:
                logger.info(f'新規主催者追加: {request.user.user_name}（集会: {community.name}）')

            # 使用済みリンクを削除
            invitation.delete()

        messages.success(request, f'{community.name} の主催者を引き継ぎました')
        return redirect('community:detail', pk=community.pk)


class RevokeOwnershipTransferView(LoginRequiredMixin, View):
    """引き継ぎリンク削除ビュー（主催者のみ）"""

    def post(self, request, pk, invitation_id):
        community = get_object_or_404(Community, pk=pk)

        # 権限チェック（主催者のみ）
        if not community.is_owner(request.user):
            messages.error(request, '権限がありません')
            return redirect('community:settings')

        invitation = get_object_or_404(
            CommunityInvitation,
            pk=invitation_id,
            community=community,
            invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER
        )
        invitation.delete()

        messages.success(request, '引き継ぎリンクを削除しました')
        logger.info(f'主催者引き継ぎリンク削除: 集会「{community.name}」、削除者: {request.user.user_name}')

        return redirect('community:settings')


class UpdateWebhookView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Webhook URL更新ビュー"""

    def test_func(self):
        community = get_object_or_404(Community, pk=self.kwargs['pk'])
        return community.can_edit(self.request.user)

    def post(self, request, pk):
        community = get_object_or_404(Community, pk=pk)
        webhook_url = request.POST.get('notification_webhook_url', '').strip()

        # 空の場合はクリア、そうでなければ検証
        if webhook_url:
            # Discord Webhook URLの形式を検証
            if not webhook_url.startswith('https://discord.com/api/webhooks/'):
                messages.error(request, 'Discord Webhook URLの形式が正しくありません。')
                return redirect('community:settings')

        community.notification_webhook_url = webhook_url if webhook_url else ''
        community.save(update_fields=['notification_webhook_url'])

        if webhook_url:
            messages.success(request, 'Discord Webhook URLを保存しました。')
        else:
            messages.success(request, 'Discord Webhook URLをクリアしました。')

        return redirect('community:settings')


class TestWebhookView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Webhookテスト送信ビュー"""

    def test_func(self):
        community = get_object_or_404(Community, pk=self.kwargs['pk'])
        return community.can_edit(self.request.user)

    def post(self, request, pk):
        community = get_object_or_404(Community, pk=pk)

        if not community.notification_webhook_url:
            messages.error(request, 'Webhook URLが設定されていません。')
            return redirect('community:settings')

        # テストメッセージを送信
        test_message = {
            "content": f"**【テスト通知】** {community.name}\n"
                       "このメッセージはテスト送信です。Webhook設定が正しく動作しています。"
        }

        webhook_timeout_seconds = 10
        try:
            response = requests.post(
                community.notification_webhook_url,
                json=test_message,
                timeout=webhook_timeout_seconds
            )
            if response.status_code == 204:
                messages.success(request, 'テスト通知を送信しました。Discordを確認してください。')
            else:
                messages.error(request, f'通知の送信に失敗しました。(ステータスコード: {response.status_code})')
        except requests.Timeout:
            messages.error(request, '通知の送信がタイムアウトしました。')
        except requests.RequestException as e:
            logger.error(f'Webhook送信エラー: {e}')
            messages.error(request, '通知の送信中にエラーが発生しました。')

        return redirect('community:settings')


class LTApplicationListView(LoginRequiredMixin, View):
    """LT申請一覧ビュー（マイリストへリダイレクト）

    旧URLへのアクセスをevent:my_listへリダイレクトする。
    """

    def get(self, request, pk):
        """旧URLへのアクセスをマイリストへリダイレクト"""
        messages.info(request, 'LT申請一覧はマイリストに統合されました。')
        return redirect('event:my_list')


class UpdateLTSettingsView(LoginRequiredMixin, UserPassesTestMixin, View):
    """LT申請設定更新ビュー"""

    def test_func(self):
        community = get_object_or_404(Community, pk=self.kwargs['pk'])
        return community.can_edit(self.request.user)

    def post(self, request, pk):
        community = get_object_or_404(Community, pk=pk)
        accepts_lt = request.POST.get('accepts_lt_application') == 'on'
        lt_template = request.POST.get('lt_application_template', '').strip()
        duration_str = request.POST.get('default_lt_duration', '30').strip()

        # デフォルト発表時間のバリデーション
        try:
            duration = max(1, int(duration_str))
        except ValueError:
            duration = 30

        community.accepts_lt_application = accepts_lt
        community.lt_application_template = lt_template
        community.default_lt_duration = duration
        community.save(update_fields=['accepts_lt_application', 'lt_application_template', 'default_lt_duration'])

        messages.success(request, 'LT申請設定を保存しました。')
        logger.info(
            f'LT申請設定更新: 集会「{community.name}」、'
            f'テンプレート文字数={len(lt_template)}、デフォルト発表時間={duration}分'
        )

        return redirect('community:settings')
