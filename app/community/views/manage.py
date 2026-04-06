"""管理系ビュー: 集会の作成、編集、承認/拒否、閉鎖/再開."""
import logging
from datetime import timedelta

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import DataError
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import UpdateView, CreateView, ListView

from event.community_cleanup import cleanup_community_future_data

from ..forms import CommunitySearchForm, CommunityUpdateForm, CommunityCreateForm
from ..libs import get_join_type
from ..models import Community, CommunityMember, WEEKDAY_CHOICES

logger = logging.getLogger(__name__)


class CommunityUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Community
    form_class = CommunityUpdateForm
    template_name = 'community/update.html'
    success_url = reverse_lazy('community:settings')

    def test_func(self):
        community = self.get_object()
        if community is None:
            return False
        return community.can_edit(self.request.user)

    def get_object(self, queryset=None):
        # セッションからactive_community_idを取得
        community_id = self.request.session.get('active_community_id')
        if community_id:
            community = Community.objects.filter(pk=community_id).first()
            if community and community.can_edit(self.request.user):
                return community

        # フォールバック: ユーザーが管理者である最初の集会を取得
        membership = self.request.user.community_memberships.select_related('community').first()
        if membership:
            return membership.community

        return None

    def form_valid(self, form):
        try:
            response = super().form_valid(form)
            calendar_entry = getattr(self.object, 'calendar_entry', None)
            if calendar_entry:
                calendar_entry.save()
            # カレンダーエントリーに関連するイベントのキャッシュを削除
            for event in self.object.events.all():
                cache_key = f'calendar_entry_url_{event.id}'
                cache.delete(cache_key)
            messages.success(self.request, '集会情報を更新しました。')
            return response
        except DataError:
            logger.exception("データの保存中にエラーが発生")
            messages.error(self.request, 'データの保存中にエラーが発生しました')
            return self.form_invalid(form)


class CommunityCreateView(LoginRequiredMixin, CreateView):
    """集会新規登録ビュー."""

    model = Community
    form_class = CommunityCreateForm
    template_name = 'community/create.html'
    success_url = reverse_lazy('account:settings')

    def form_valid(self, form):
        response = super().form_valid(form)

        # オーナーとしてCommunityMemberを作成
        CommunityMember.objects.create(
            community=self.object,
            user=self.request.user,
            role=CommunityMember.Role.OWNER
        )

        # Discord通知を送信
        if settings.DISCORD_WEBHOOK_URL:
            waiting_list_url = self.request.build_absolute_uri(reverse('community:waiting_list'))
            discord_message = {
                "content": f"**【新規集会登録】** {self.object.name}\n"
                           f"承認ページ: {waiting_list_url}"
            }
            discord_timeout_seconds = 10
            try:
                requests.post(settings.DISCORD_WEBHOOK_URL, json=discord_message, timeout=discord_timeout_seconds)
            except Exception as e:
                logger.warning(f'Discord通知送信失敗: {e}')

        messages.success(self.request, '集会が登録されました。承認後に公開されます。')
        return response


class WaitingCommunityListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = Community
    template_name = 'community/waiting_list.html'
    context_object_name = 'communities'

    def test_func(self):
        return self.request.user.is_superuser

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = queryset.filter(status='pending')
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = CommunitySearchForm(self.request.GET)
        context['form'] = form
        context['search_count'] = self.get_queryset().count()
        for community in context['communities']:
            community.join_type = get_join_type(community.organizer_url)

        # 曜日の選択肢をコンテキストに追加
        context['weekday_choices'] = dict(WEEKDAY_CHOICES)

        return context


class AcceptView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not request.user.is_superuser:
            messages.error(request, '権限がありません。')
            return redirect('community:waiting_list')

        community = get_object_or_404(Community, pk=pk)
        community.status = 'approved'
        community.save()

        # 承認通知メールを送信
        subject = f'{community.name}が承認されました'
        my_list_url = request.build_absolute_uri(reverse('event:my_list'))
        context = {
            'community': community,
            'my_list_url': my_list_url,
            'owner_name': community.get_owner().user_name if community.get_owner() else None,
        }

        # HTMLメールを生成
        html_message = render_to_string('community/email/accept.html', context)

        owner_email = community.get_owner_email()
        if owner_email:
            sent = send_mail(
                subject=subject,
                message='',  # プレーンテキストは空文字列を設定
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[owner_email],
                html_message=html_message,
            )
            if sent:
                logger.info(f'承認メール送信成功: {community.name} to {owner_email}')
            else:
                logger.warning(f'承認メール送信失敗: {community.name} to {owner_email}')
        else:
            logger.warning(f'承認メール送信スキップ: {community.name} - オーナーのメールアドレスが見つかりません')

        messages.success(request, f'{community.name}を承認しました。')
        return redirect('community:waiting_list')


class RejectView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not request.user.is_superuser:
            messages.error(request, '権限がありません。')
            return redirect('community:waiting_list')

        community = get_object_or_404(Community, pk=pk)
        community.status = 'rejected'
        community.save()

        # 非承認メールを送信
        subject = f'{community.name}が非承認になりました'
        context = {
            'community': community,
            'owner_name': community.get_owner().user_name if community.get_owner() else None,
        }

        # HTMLメールを生成
        html_message = render_to_string('community/email/reject.html', context)

        owner_email = community.get_owner_email()
        if owner_email:
            sent = send_mail(
                subject=subject,
                message='',  # プレーンテキストは空文字列を設定
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[owner_email],
                html_message=html_message,
            )
            if sent:
                logger.info(f'非承認メール送信成功: {community.name} to {owner_email}')
            else:
                logger.warning(f'非承認メール送信失敗: {community.name} to {owner_email}')
        else:
            logger.warning(f'非承認メール送信スキップ: {community.name} - オーナーのメールアドレスが見つかりません')

        messages.success(request, f'{community.name}を非承認にしました。')
        return redirect('community:waiting_list')


class CloseCommunityView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        community = get_object_or_404(Community, pk=self.kwargs['pk'])
        return self.request.user.is_superuser or community.is_owner(self.request.user)

    def get(self, request, pk):
        messages.warning(request, 'この操作はPOSTリクエストでのみ実行できます。')
        return redirect('community:detail', pk=pk)

    def post(self, request, pk):
        community = get_object_or_404(Community, pk=pk)

        # 権限チェック（主催者のみ閉鎖可能）
        if not (request.user.is_superuser or community.can_delete(request.user)):
            messages.error(request, '権限がありません。')
            return redirect('community:detail', pk=pk)

        # 閉鎖日を設定（今日の日付）
        today = timezone.now().date()
        community.end_at = today
        community.save()

        # 当日開催分は残し、翌日以降の予定を停止対象にする
        cleanup_from_date = today + timedelta(days=1)
        try:
            stats = cleanup_community_future_data(
                community=community,
                from_date=cleanup_from_date,
                delete_rules=True,
                delete_google_events=True,
                google_window_days=365,
                google_years=1,
            )

            logger.info(
                f'集会「{community.name}」を閉鎖しました。'
                f'削除イベント数={stats["db_events"]}、'
                f'削除定期ルール数={stats["rules"]}、'
                f'削除Googleイベント数={stats["google_events"]}'
            )
            messages.success(
                request,
                f'{community.name}を閉鎖しました。'
                f'{stats["db_events"]}件のイベント、{stats["rules"]}件の定期ルール、'
                f'{stats["google_events"]}件のGoogleイベントを削除しました。'
            )
        except Exception as e:
            logger.exception(f'集会「{community.name}」の閉鎖後クリーンアップでエラー: {e}')
            messages.warning(
                request,
                f'{community.name}は閉鎖しましたが、関連データ削除中にエラーが発生しました。'
                '管理者に連絡してください。'
            )

        return redirect('community:detail', pk=pk)


class AdminCommunityCleanupView(LoginRequiredMixin, UserPassesTestMixin, View):
    """管理者用: 集会停止と関連データ削除をワンクリックで実行する。"""

    def test_func(self):
        return self.request.user.is_superuser

    def get(self, request, pk):
        messages.warning(request, 'この操作はPOSTリクエストでのみ実行できます。')
        return redirect('community:detail', pk=pk)

    def post(self, request, pk):
        community = get_object_or_404(Community, pk=pk)
        today = timezone.now().date()

        # 閉鎖されていない場合は閉鎖状態にする
        if community.end_at is None:
            community.end_at = today
            community.save(update_fields=['end_at'])

        # 当日開催分は残し、翌日以降を停止対象にする
        cleanup_from_date = today + timedelta(days=1)
        try:
            stats = cleanup_community_future_data(
                community=community,
                from_date=cleanup_from_date,
                delete_rules=True,
                delete_google_events=True,
                google_window_days=365,
                google_years=1,
            )

            messages.success(
                request,
                f'管理者クリーンアップ完了: {community.name} / '
                f'イベント{stats["db_events"]}件・定期ルール{stats["rules"]}件・'
                f'Googleイベント{stats["google_events"]}件を削除しました。'
            )
        except Exception as e:
            logger.exception(f'管理者クリーンアップでエラー: community={community.name}, error={e}')
            messages.error(
                request,
                '管理者クリーンアップでエラーが発生しました。ログを確認してください。'
            )
        return redirect('community:detail', pk=pk)


class ReopenCommunityView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        community = get_object_or_404(Community, pk=self.kwargs['pk'])
        return self.request.user.is_superuser or community.is_owner(self.request.user)

    def post(self, request, pk):
        community = get_object_or_404(Community, pk=pk)

        # 権限チェック（主催者のみ再開可能）
        if not (request.user.is_superuser or community.can_delete(request.user)):
            messages.error(request, '権限がありません。')
            return redirect('community:detail', pk=pk)

        # 閉鎖日をクリア
        community.end_at = None
        community.save()

        logger.info(f'集会「{community.name}」を再開しました。')
        messages.success(request, f'{community.name}を再開しました。')

        return redirect('community:detail', pk=pk)
