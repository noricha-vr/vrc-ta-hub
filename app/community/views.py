# app/community/views.py
import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.mixins import UserPassesTestMixin
from django.db import DataError
from django.db.models import Min, Q, F
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, DetailView
from django.views.generic import UpdateView
from django.core.mail import send_mail
from django.conf import settings
from django.template.loader import render_to_string
from django.core.cache import cache

from event.models import Event
from url_filters import get_filtered_url
from .forms import CommunitySearchForm
from .forms import CommunityUpdateForm
from .libs import get_join_type
from .models import Community

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
        queryset = queryset.filter(status='approved', end_at__isnull=True)

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
            details = event.details.all()
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
        return self.request.user == community.custom_user

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
