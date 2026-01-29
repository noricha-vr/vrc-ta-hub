# app/community/views.py
import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.mixins import UserPassesTestMixin
from django.db import DataError
from django.db.models import Min, Q, F, Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, DetailView
from django.views.generic import UpdateView, CreateView
from django.core.mail import send_mail
from django.conf import settings
from django.template.loader import render_to_string
from django.core.cache import cache
import requests

from event.models import Event, EventDetail
from url_filters import get_filtered_url
from django.views.generic import TemplateView
from user_account.models import CustomUser

from .forms import CommunitySearchForm
from .forms import CommunityUpdateForm
from .forms import CommunityCreateForm
from .libs import get_join_type
from .models import Community, CommunityMember, CommunityInvitation

logger = logging.getLogger(__name__)


# app/community/views.py

class CommunityListView(ListView):
    model = Community
    template_name = 'community/list.html'
    context_object_name = 'communities'
    paginate_by = 18

    def get(self, request, *args, **kwargs):
        # 通常のget処理の前にページ番号をチェック
        page = request.GET.get('page', 1)
        self.object_list = self.get_queryset()

        paginator = self.get_paginator(self.object_list, self.paginate_by)
        if int(page) > paginator.num_pages and paginator.num_pages > 0:
            # クエリパラメータを維持したまま1ページ目にリダイレクト
            params = request.GET.copy()
            params['page'] = 1
            return redirect(f"{request.path}?{params.urlencode()}")

        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset()
        now = timezone.now()
        # 承認済みでアクティブな集会（終了日がない）、かつポスター画像がある
        queryset = queryset.filter(
            status='approved', 
            end_at__isnull=True,
            poster_image__isnull=False
        ).exclude(poster_image='')

        # 最新のイベント日を取得
        queryset = queryset.annotate(
            latest_event_date=Min(
                'events__date',
                filter=Q(events__date__gte=now.date())
            )
        )

        form = CommunitySearchForm(self.request.GET)
        if form.is_valid():
            if query := form.cleaned_data['query']:
                queryset = queryset.filter(Q(name__icontains=query) | Q(description__icontains=query))
            if weekdays := form.cleaned_data['weekdays']:
                weekday_filters = Q()
                for weekday in weekdays:
                    weekday_filters |= Q(weekdays__contains=weekday)
                queryset = queryset.filter(weekday_filters)
            if tags := form.cleaned_data['tags']:
                # タグフィルタリングの修正
                tag_filters = Q()
                for tag in tags:
                    tag_filters |= Q(tags__contains=[tag])
                queryset = queryset.filter(tag_filters)

        # 最新のイベント日でソート（NULL値は最後に）
        queryset = queryset.order_by(
            F('latest_event_date').asc(nulls_last=True),
            '-updated_at'
        )
        logger.info(f'検索結果: {queryset.count()}件')
        if queryset.count() == 0:
            logger.info('現在開催中の集会はありません。')
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = CommunitySearchForm(self.request.GET or None)
        context['selected_weekdays'] = self.request.GET.getlist('weekdays')
        context['selected_tags'] = self.request.GET.getlist('tags')

        base_url = reverse('community:list')
        current_params = self.request.GET.copy()

        # ページネーションリンク用に既存の 'page' パラメータを削除
        query_params_for_pagination = current_params.copy()
        if 'page' in query_params_for_pagination:
            del query_params_for_pagination['page']
        context['current_query_params'] = query_params_for_pagination.urlencode()

        context['weekday_urls'] = {
            choice[0]: get_filtered_url(base_url, current_params, 'weekdays', choice[0])
            for choice in context['form'].fields['weekdays'].choices
        }
        context['tag_urls'] = {
            choice[0]: get_filtered_url(base_url, current_params, 'tags', choice[0])
            for choice in context['form'].fields['tags'].choices
        }

        # 曜日の選択肢をコンテキストに追加
        context['weekday_choices'] = dict(WEEKDAY_CHOICES)
        # タグの選択肢をコンテキストに追加
        context['tag_choices'] = dict(TAGS)
        # 検索結果の件数をコンテキストに追加
        context['search_count'] = self.get_queryset().count()
        return context


from .models import Community, WEEKDAY_CHOICES, TAGS


class CommunityDetailView(DetailView):
    model = Community
    template_name = 'community/detail.html'
    context_object_name = 'community'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        community = context['community']
        if community.twitter_hashtag:
            community.twitter_hashtags = [f'#{tag.strip()}' for tag in community.twitter_hashtag.split('#') if
                                          tag.strip()]
        community.join_type = get_join_type(community.organizer_url)

        now = timezone.now()

        # 予定されているイベント scheduled events
        scheduled_events = Event.objects.filter(
            community=community, date__gte=now).prefetch_related('details').order_by('date', 'start_time')[:4]
        context['scheduled_events'] = self.get_event_details(scheduled_events)

        # 過去のイベントを取得。ただし、event_detailが存在するもののみ
        past_events = Event.objects.filter(
            community=community, date__lt=now
        ).filter(
            Q(details__theme__isnull=False) | Q(details__theme__gt='')
        ).prefetch_related('details').order_by('-date', '-start_time')[:6]
        context['past_events'] = self.get_event_details(past_events)

        # 曜日の選択肢をコンテキストに追加
        context['weekday_choices'] = dict(WEEKDAY_CHOICES)

        # タグの選択肢をコンテキストに追加
        context['tag_choices'] = dict(TAGS)

        # 承認ボタンの表示
        if self.request.user.is_superuser and community.status == 'pending':
            context['show_accept_button'] = True
            context['show_reject_button'] = True

        return context

    def get_event_details(self, events):
        event_details_list = []
        last_event = None
        for event in events:
            # 承認済みのEventDetailのみ表示
            details = event.details.filter(status='approved')
            if event == last_event:
                continue
            event_details_list.append({
                'details': details,
                'event': event,
            })
            last_event = event
        return event_details_list


class CommunityUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Community
    form_class = CommunityUpdateForm
    template_name = 'community/update.html'
    success_url = reverse_lazy('account:settings')

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

        # 後方互換: custom_userを使用
        return Community.objects.filter(custom_user=self.request.user).first()

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
            messages.success(self.request, '集会情報とVRCイベントカレンダー用情報が更新されました。')
            return response
        except DataError as e:
            messages.error(self.request, f'データの保存中にエラーが発生しました: {str(e)}')
            return self.form_invalid(form)


class CommunityCreateView(LoginRequiredMixin, CreateView):
    """集会新規登録ビュー."""

    model = Community
    form_class = CommunityCreateForm
    template_name = 'community/create.html'
    success_url = reverse_lazy('account:settings')

    def form_valid(self, form):
        form.instance.custom_user = self.request.user
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


class WaitingCommunityListView(LoginRequiredMixin, ListView):
    model = Community
    template_name = 'community/waiting_list.html'
    context_object_name = 'communities'

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
            if community.twitter_hashtag:
                community.twitter_hashtags = [f'#{tag.strip()}' for tag in community.twitter_hashtag.split('#') if
                                              tag.strip()]
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
        }

        # HTMLメールを生成
        html_message = render_to_string('community/email/accept.html', context)

        sent = send_mail(
            subject=subject,
            message='',  # プレーンテキストは空文字列を設定
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[community.custom_user.email],
            html_message=html_message,
        )
        if sent:
            logger.info(f'承認メール送信成功: {community.name} to {community.custom_user.email}')
        else:
            logger.warning(f'承認メール送信失敗: {community.name} to {community.custom_user.email}')

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
        }

        # HTMLメールを生成
        html_message = render_to_string('community/email/reject.html', context)

        sent = send_mail(
            subject=subject,
            message='',  # プレーンテキストは空文字列を設定
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[community.custom_user.email],
            html_message=html_message,
        )
        if sent:
            logger.info(f'非承認メール送信成功: {community.name} to {community.custom_user.email}')
        else:
            logger.warning(f'非承認メール送信失敗: {community.name} to {community.custom_user.email}')

        messages.success(request, f'{community.name}を非承認にしました。')
        return redirect('community:waiting_list')


class CloseCommunityView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        community = get_object_or_404(Community, pk=self.kwargs['pk'])
        return self.request.user.is_superuser or community.is_owner(self.request.user)

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
        
        # 閉鎖日以降のイベントを削除
        deleted_count = Event.objects.filter(
            community=community,
            date__gt=today
        ).delete()[0]
        
        # 定期ルールを削除
        from event.models import RecurrenceRule
        deleted_rules = RecurrenceRule.objects.filter(community=community).delete()[0]
        
        logger.info(f'集会「{community.name}」を閉鎖しました。削除されたイベント数: {deleted_count}、削除された定期ルール数: {deleted_rules}')
        messages.success(request, f'{community.name}を閉鎖しました。{deleted_count}件のイベントと{deleted_rules}件の定期ルールが削除されました。')
        
        # バックグラウンドでGoogleカレンダーを同期
        import threading
        from event.sync_to_google import DatabaseToGoogleSync
        
        def sync_calendar_in_background():
            try:
                logger.info(f'集会「{community.name}」の閉鎖に伴い、Googleカレンダーの同期を開始します。')
                sync = DatabaseToGoogleSync()
                stats = sync.sync_all_communities(months_ahead=3)
                logger.info(f'Googleカレンダーの同期が完了しました。削除: {stats["deleted"]}件')
            except Exception as e:
                logger.error(f'Googleカレンダーの同期中にエラーが発生しました: {str(e)}')
        
        thread = threading.Thread(target=sync_calendar_in_background)
        thread.daemon = True
        thread.start()
        
        messages.info(request, 'Googleカレンダーの同期をバックグラウンドで実行しています。')
        
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


class ArchivedCommunityListView(ListView):
    model = Community
    template_name = 'community/archive_list.html'
    context_object_name = 'communities'
    paginate_by = 18

    def get_queryset(self):
        queryset = super().get_queryset()
        # 承認済みで閉鎖された集会のみ表示
        queryset = queryset.filter(
            status='approved',
            end_at__isnull=False
        ).order_by('-end_at')

        form = CommunitySearchForm(self.request.GET)
        if form.is_valid():
            if query := form.cleaned_data['query']:
                queryset = queryset.filter(Q(name__icontains=query) | Q(description__icontains=query))
            if weekdays := form.cleaned_data['weekdays']:
                weekday_filters = Q()
                for weekday in weekdays:
                    weekday_filters |= Q(weekdays__contains=weekday)
                queryset = queryset.filter(weekday_filters)
            if tags := form.cleaned_data['tags']:
                tag_filters = Q()
                for tag in tags:
                    tag_filters |= Q(tags__contains=[tag])
                queryset = queryset.filter(tag_filters)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = CommunitySearchForm(self.request.GET or None)
        context['selected_weekdays'] = self.request.GET.getlist('weekdays')
        context['selected_tags'] = self.request.GET.getlist('tags')

        base_url = reverse('community:archive_list')
        current_params = self.request.GET.copy()

        # ページネーションリンク用に既存の 'page' パラメータを削除
        query_params_for_pagination = current_params.copy()
        if 'page' in query_params_for_pagination:
            del query_params_for_pagination['page']
        context['current_query_params'] = query_params_for_pagination.urlencode()

        context['weekday_urls'] = {
            choice[0]: get_filtered_url(base_url, current_params, 'weekdays', choice[0])
            for choice in context['form'].fields['weekdays'].choices
        }
        context['tag_urls'] = {
            choice[0]: get_filtered_url(base_url, current_params, 'tags', choice[0])
            for choice in context['form'].fields['tags'].choices
        }

        context['weekday_choices'] = dict(WEEKDAY_CHOICES)
        context['tag_choices'] = dict(TAGS)
        context['search_count'] = self.get_queryset().count()
        return context


class SwitchCommunityView(LoginRequiredMixin, View):
    """アクティブな集会を切り替えるビュー"""

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

        if success and redirect_to:
            return redirect_to
        if referer:
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
            if request.user.community_memberships.filter(community_id=community_id_int).exists():
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

        invitation = CommunityInvitation.create_invitation(community, request.user)
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

        # 後方互換: custom_userとして関連付けられた集会
        community = Community.objects.filter(custom_user=self.request.user).first()
        if community:
            return community, None

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
        elif community and community.custom_user == self.request.user:
            # 後方互換: custom_userは主催者とみなす
            context['is_owner'] = True
        else:
            context['is_owner'] = False

        # 有効な引き継ぎリンクを取得
        if context['is_owner'] and community:
            context['ownership_transfer_invitation'] = CommunityInvitation.objects.filter(
                community=community,
                invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER,
                expires_at__gt=timezone.now()
            ).first()

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
        from datetime import timedelta
        from .models import INVITATION_EXPIRATION_DAYS
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

            # 後方互換性: custom_user フィールドも更新
            community.custom_user = request.user
            community.save()

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
