"""管理系ビュー: 集会の作成、編集、承認/拒否、閉鎖/再開."""
import logging

import requests  # noqa: F401 - 既存テストの patch パス互換用
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import DataError
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import UpdateView, CreateView, ListView

from event.community_cleanup import cleanup_community_future_data
from ta_hub.access_mixins import AuthenticatedForbiddenMixin
from user_account.vrchat import normalize_vrchat_user_id

from ..forms_processor import (
    approve_community_registration,
    cleanup_closed_community,
    close_community_and_cleanup,
    create_owner_membership,
    notify_new_community_registration,
    refresh_calendar_entry_and_event_cache,
    reject_community_registration,
)
from ..forms import CommunitySearchForm, CommunityUpdateForm, CommunityCreateForm
from ..libs import get_join_type
from ..models import Community, WEEKDAY_CHOICES

logger = logging.getLogger(__name__)


class CommunityUpdateView(LoginRequiredMixin, AuthenticatedForbiddenMixin, UpdateView):
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
            refresh_calendar_entry_and_event_cache(self.object)
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
        organizer_url = form.cleaned_data.get('organizer_url', '')
        if organizer_url:
            try:
                self.request.user.vrchat_user_id = normalize_vrchat_user_id(organizer_url)
                self.request.user.save(update_fields=['vrchat_user_id'])
            except ValidationError:
                pass

        create_owner_membership(self.object, self.request.user)
        notify_new_community_registration(self.object, self.request)

        messages.success(self.request, '集会が登録されました。承認後に公開されます。')
        return response


class WaitingCommunityListView(LoginRequiredMixin, AuthenticatedForbiddenMixin, ListView):
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
        approve_community_registration(community, request)

        messages.success(request, f'{community.name}を承認しました。')
        return redirect('community:waiting_list')


class RejectView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not request.user.is_superuser:
            messages.error(request, '権限がありません。')
            return redirect('community:waiting_list')

        community = get_object_or_404(Community, pk=pk)
        reject_community_registration(community)

        messages.success(request, f'{community.name}を非承認にしました。')
        return redirect('community:waiting_list')


class CloseCommunityView(LoginRequiredMixin, AuthenticatedForbiddenMixin, View):
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

        try:
            stats = close_community_and_cleanup(
                community,
                cleanup_func=cleanup_community_future_data,
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


class AdminCommunityCleanupView(LoginRequiredMixin, AuthenticatedForbiddenMixin, View):
    """管理者用: 集会停止と関連データ削除をワンクリックで実行する。"""

    def test_func(self):
        return self.request.user.is_superuser

    def get(self, request, pk):
        messages.warning(request, 'この操作はPOSTリクエストでのみ実行できます。')
        return redirect('community:detail', pk=pk)

    def post(self, request, pk):
        community = get_object_or_404(Community, pk=pk)

        try:
            stats = cleanup_closed_community(
                community,
                cleanup_func=cleanup_community_future_data,
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


class ReopenCommunityView(LoginRequiredMixin, AuthenticatedForbiddenMixin, View):
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
