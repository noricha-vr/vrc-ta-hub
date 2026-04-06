from datetime import timedelta

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
from django.views.generic import CreateView, ListView, UpdateView

from community.libs import get_join_type
from community.models import Community, CommunityMember, WEEKDAY_CHOICES

from ..forms import CommunityCreateForm, CommunitySearchForm, CommunityUpdateForm

import community.views as community_views


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
        community_id = self.request.session.get('active_community_id')
        if community_id:
            community = Community.objects.filter(pk=community_id).first()
            if community and community.can_edit(self.request.user):
                return community

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
            for event in self.object.events.all():
                cache_key = f'calendar_entry_url_{event.id}'
                cache.delete(cache_key)
            messages.success(self.request, '集会情報を更新しました。')
            return response
        except DataError:
            community_views.logger.exception("データの保存中にエラーが発生")
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

        CommunityMember.objects.create(
            community=self.object,
            user=self.request.user,
            role=CommunityMember.Role.OWNER,
        )

        if settings.DISCORD_WEBHOOK_URL:
            waiting_list_url = self.request.build_absolute_uri(reverse('community:waiting_list'))
            discord_message = {
                "content": f"**【新規集会登録】** {self.object.name}\n"
                f"承認ページ: {waiting_list_url}"
            }
            discord_timeout_seconds = 10
            try:
                community_views.requests.post(
                    settings.DISCORD_WEBHOOK_URL,
                    json=discord_message,
                    timeout=discord_timeout_seconds,
                )
            except Exception as exc:
                community_views.logger.warning(f'Discord通知送信失敗: {exc}')

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
        return queryset.filter(status='pending')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = CommunitySearchForm(self.request.GET)
        context['search_count'] = self.get_queryset().count()
        for community in context['communities']:
            community.join_type = get_join_type(community.organizer_url)

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

        subject = f'{community.name}が承認されました'
        my_list_url = request.build_absolute_uri(reverse('event:my_list'))
        context = {
            'community': community,
            'my_list_url': my_list_url,
            'owner_name': community.get_owner().user_name if community.get_owner() else None,
        }

        html_message = render_to_string('community/email/accept.html', context)

        owner_email = community.get_owner_email()
        if owner_email:
            sent = send_mail(
                subject=subject,
                message='',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[owner_email],
                html_message=html_message,
            )
            if sent:
                community_views.logger.info(f'承認メール送信成功: {community.name} to {owner_email}')
            else:
                community_views.logger.warning(f'承認メール送信失敗: {community.name} to {owner_email}')
        else:
            community_views.logger.warning(
                f'承認メール送信スキップ: {community.name} - オーナーのメールアドレスが見つかりません'
            )

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

        subject = f'{community.name}が非承認になりました'
        context = {
            'community': community,
            'owner_name': community.get_owner().user_name if community.get_owner() else None,
        }

        html_message = render_to_string('community/email/reject.html', context)

        owner_email = community.get_owner_email()
        if owner_email:
            sent = send_mail(
                subject=subject,
                message='',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[owner_email],
                html_message=html_message,
            )
            if sent:
                community_views.logger.info(f'非承認メール送信成功: {community.name} to {owner_email}')
            else:
                community_views.logger.warning(f'非承認メール送信失敗: {community.name} to {owner_email}')
        else:
            community_views.logger.warning(
                f'非承認メール送信スキップ: {community.name} - オーナーのメールアドレスが見つかりません'
            )

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

        if not (request.user.is_superuser or community.can_delete(request.user)):
            messages.error(request, '権限がありません。')
            return redirect('community:detail', pk=pk)

        today = timezone.now().date()
        community.end_at = today
        community.save()

        cleanup_from_date = today + timedelta(days=1)
        try:
            stats = community_views.cleanup_community_future_data(
                community=community,
                from_date=cleanup_from_date,
                delete_rules=True,
                delete_google_events=True,
                google_window_days=365,
                google_years=1,
            )

            community_views.logger.info(
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
        except Exception as exc:
            community_views.logger.exception(
                f'集会「{community.name}」の閉鎖後クリーンアップでエラー: {exc}'
            )
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

        if community.end_at is None:
            community.end_at = today
            community.save(update_fields=['end_at'])

        cleanup_from_date = today + timedelta(days=1)
        try:
            stats = community_views.cleanup_community_future_data(
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
        except Exception as exc:
            community_views.logger.exception(
                f'管理者クリーンアップでエラー: community={community.name}, error={exc}'
            )
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

        if not (request.user.is_superuser or community.can_delete(request.user)):
            messages.error(request, '権限がありません。')
            return redirect('community:detail', pk=pk)

        community.end_at = None
        community.save()

        community_views.logger.info(f'集会「{community.name}」を再開しました。')
        messages.success(request, f'{community.name}を再開しました。')

        return redirect('community:detail', pk=pk)
