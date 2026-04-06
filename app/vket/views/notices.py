from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from allauth.socialaccount.models import SocialAccount

from community.models import Community, CommunityMember

from ..models import VketCollaboration, VketNotice, VketNoticeReceipt, VketParticipation
from .common import _get_active_membership, _is_vket_admin


class NoticeListView(LoginRequiredMixin, View):
    """主催者向け: 自分の参加に届いたお知らせ一覧ビュー"""

    template_name = 'vket/notice_list.html'

    def get(self, request, pk: int):
        collaboration = get_object_or_404(VketCollaboration, pk=pk)
        community, membership = _get_active_membership(request)

        receipts = []
        if community:
            participation = VketParticipation.objects.filter(
                collaboration=collaboration, community=community
            ).first()
            if participation:
                receipts = (
                    VketNoticeReceipt.objects.filter(participation=participation)
                    .select_related('notice')
                    .order_by('-created_at')
                )

        return render(
            request,
            self.template_name,
            {
                'collaboration': collaboration,
                'receipts': receipts,
                'community': community,
            },
        )


class ManageNoticeListView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """運営向け: お知らせ管理一覧ビュー"""

    template_name = 'vket/manage_notice_list.html'

    def test_func(self):
        return _is_vket_admin(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        collaboration = get_object_or_404(VketCollaboration, pk=kwargs['pk'])

        # 各お知らせの配送状況を集計
        notices = (
            VketNotice.objects.filter(collaboration=collaboration)
            .prefetch_related('receipts__participation__community')
            .order_by('-created_at')
        )

        # prefetch済みのreceiptsをPython側で集計してN+1を回避
        notice_stats = []
        for notice in notices:
            receipts = list(notice.receipts.all())
            total = len(receipts)
            acked = sum(1 for r in receipts if r.acknowledged_at is not None)

            # 未ACKのコミュニティからDiscordメンション文字列を収集
            unacked_mentions = []
            unacked_community_names = []
            if notice.requires_ack:
                seen_community_ids = set()
                for r in receipts:
                    if r.acknowledged_at is not None:
                        continue
                    community = r.participation.community
                    if community.id in seen_community_ids:
                        continue
                    seen_community_ids.add(community.id)
                    unacked_community_names.append(community.name)
                    mention_type = community.discord_mention_type
                    if mention_type == Community.DiscordMentionType.ROLE and community.discord_mention_role_id:
                        unacked_mentions.append(f'<@&{community.discord_mention_role_id}>')
                    elif mention_type == Community.DiscordMentionType.USERS:
                        for uid in community.discord_mention_user_ids:
                            unacked_mentions.append(f'<@{uid}>')
                    else:
                        # メンション未設定: メンバーのDiscord IDからメンション生成
                        member_user_ids = CommunityMember.objects.filter(
                            community=community
                        ).values_list('user_id', flat=True)
                        discord_ids = SocialAccount.objects.filter(
                            user_id__in=member_user_ids, provider='discord'
                        ).values_list('uid', flat=True)
                        for did in discord_ids:
                            unacked_mentions.append(f'<@{did}>')

            notice_stats.append(
                {
                    'notice': notice,
                    'total': total,
                    'acked': acked,
                    'unacked_mentions': unacked_mentions,
                    'unacked_community_names': unacked_community_names,
                }
            )

        context.update(
            {
                'collaboration': collaboration,
                'notice_stats': notice_stats,
            }
        )
        return context


class ManageNoticeCreateView(LoginRequiredMixin, UserPassesTestMixin, View):
    """運営向け: お知らせ作成ビュー"""

    def test_func(self):
        return _is_vket_admin(self.request.user)

    def post(self, request, pk: int):
        collaboration = get_object_or_404(VketCollaboration, pk=pk)
        title = request.POST.get('title', '').strip()
        body = request.POST.get('body', '').strip()
        target_scope = request.POST.get('target_scope', VketNotice.TargetScope.ALL_PARTICIPANTS)
        requires_ack = bool(request.POST.get('requires_ack'))

        if not title or not body:
            messages.error(request, 'タイトルと本文は必須です。')
            return redirect('vket:manage_notice_list', pk=pk)

        if target_scope not in dict(VketNotice.TargetScope.choices):
            target_scope = VketNotice.TargetScope.ALL_PARTICIPANTS

        notice = VketNotice.objects.create(
            collaboration=collaboration,
            title=title,
            body=body,
            target_scope=target_scope,
            requires_ack=requires_ack,
            created_by=request.user,
        )

        # 対象参加者にReceiptを自動生成
        participations = VketParticipation.objects.filter(
            collaboration=collaboration,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
        )
        if target_scope == VketNotice.TargetScope.UNACKED:
            participations = participations.filter(last_acknowledged_at__isnull=True)

        receipts = [
            VketNoticeReceipt(notice=notice, participation=p)
            for p in participations
        ]
        VketNoticeReceipt.objects.bulk_create(receipts)

        messages.success(request, 'お知らせを作成しました。')
        return redirect('vket:manage_notice_list', pk=pk)


class ManageNoticeUpdateView(LoginRequiredMixin, UserPassesTestMixin, View):
    """運営向け: お知らせ編集ビュー"""

    def test_func(self):
        return _is_vket_admin(self.request.user)

    def post(self, request, pk: int, notice_id: int):
        collaboration = get_object_or_404(VketCollaboration, pk=pk)
        notice = get_object_or_404(VketNotice, pk=notice_id, collaboration=collaboration)

        title = request.POST.get('title', '').strip()
        body = request.POST.get('body', '').strip()

        if not title or not body:
            messages.error(request, 'タイトルと本文は必須です。')
            return redirect('vket:manage_notice_list', pk=pk)

        notice.title = title
        notice.body = body
        notice.save(update_fields=['title', 'body'])

        messages.success(request, 'お知らせを更新しました。')
        return redirect('vket:manage_notice_list', pk=pk)


class AckNoticeView(View):
    """ログイン不要: お知らせ確認（ACK）ビュー

    GET: お知らせ内容を表示し確認ボタンを提示（状態変更なし）
    POST: 確認済みに変更（Discordプレビュー・スキャナによる誤ACK防止）
    """

    template_name = 'vket/ack_notice.html'

    def get(self, request, ack_token):
        receipt = get_object_or_404(
            VketNoticeReceipt.objects.select_related('notice', 'participation'),
            ack_token=ack_token,
        )
        return render(
            request,
            self.template_name,
            {
                'receipt': receipt,
                'notice': receipt.notice,
                'already_acked': receipt.acknowledged_at is not None,
                'collaboration_id': receipt.participation.collaboration_id,
            },
        )

    def post(self, request, ack_token):
        receipt = get_object_or_404(VketNoticeReceipt, ack_token=ack_token)
        already_acked = receipt.acknowledged_at is not None

        if not already_acked:
            now = timezone.now()
            receipt.acknowledged_at = now
            # ログイン中であれば確認者を記録
            if request.user.is_authenticated:
                receipt.acknowledged_by = request.user
            receipt.save(update_fields=['acknowledged_at', 'acknowledged_by', 'updated_at'])

            # 参加レコードのlast_acknowledged_at/byも更新（進捗管理・監査用）
            participation = receipt.participation
            participation.last_acknowledged_at = now
            if request.user.is_authenticated:
                participation.last_acknowledged_by = request.user
            participation.save(update_fields=['last_acknowledged_at', 'last_acknowledged_by', 'updated_at'])

        return render(
            request,
            self.template_name,
            {
                'receipt': receipt,
                'notice': receipt.notice,
                'already_acked': True,
                'collaboration_id': receipt.participation.collaboration_id,
            },
        )
