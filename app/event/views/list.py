import logging

from django.db.models import Prefetch
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import ListView

from event.forms import EventSearchForm
from event.models import Event, EventDetail
from event_calendar.calendar_utils import generate_google_calendar_url
from url_filters import get_filtered_url
from utils.vrchat_time import get_vrchat_today
from website.settings import GOOGLE_CALENDAR_ID

from ta_hub.utils import get_client_ip
from django.core.cache import cache
from django.http import HttpResponse
from django.utils import timezone

logger = logging.getLogger(__name__)


class EventListView(ListView):
    model = Event
    template_name = 'event/list.html'
    context_object_name = 'events'
    paginate_by = 30

    def get(self, request, *args, **kwargs):
        # 通常のget処理の前にページ番号をチェック
        page_str = request.GET.get('page', '1')

        try:
            # ページ番号のみを抽出（数字以外を除去）
            page = int(''.join(filter(str.isdigit, page_str)) or '1')
        except (ValueError, TypeError):
            # 無効なページ番号の場合は1ページ目にリダイレクト
            params = request.GET.copy()
            params['page'] = '1'
            return redirect(f"{request.path}?{params.urlencode()}")

        self.object_list = self.get_queryset()
        paginator = self.get_paginator(self.object_list, self.paginate_by)

        if page > paginator.num_pages and paginator.num_pages > 0:
            # 存在しないページ番号の場合は1ページ目にリダイレクト
            params = request.GET.copy()
            params['page'] = '1'
            return redirect(f"{request.path}?{params.urlencode()}")

        # ページ番号が有効な場合は通常の処理を続行
        request.GET = request.GET.copy()
        request.GET['page'] = str(page)
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset()
        # VRChatterの生活リズムに合わせて朝4時を日付の境界とする
        today = get_vrchat_today()
        queryset = queryset.filter(
            date__gte=today,
            community__status='approved',
            community__end_at__isnull=True,
        ).select_related('community').prefetch_related(
            Prefetch('details', queryset=EventDetail.objects.filter(status='approved'))
        ).order_by('date', 'start_time')

        form = EventSearchForm(self.request.GET)
        if form.is_valid():
            if name := form.cleaned_data.get('name'):
                queryset = queryset.filter(community__name__icontains=name)

            if weekdays := form.cleaned_data.get('weekday'):
                queryset = queryset.filter(weekday__in=weekdays)

            if tags := form.cleaned_data['tags']:
                for tag in tags:
                    queryset = queryset.filter(community__tags__contains=[tag])

        # 各イベントにGoogleカレンダー追加用URLを設定
        for event in queryset:
            event.google_calendar_url = generate_google_calendar_url(self.request, event)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = EventSearchForm(self.request.GET or None)
        context['selected_weekdays'] = self.request.GET.getlist('weekday')
        context['selected_tags'] = self.request.GET.getlist('tags')

        base_url = reverse('event:list')
        current_params = self.request.GET.copy()

        # ページネーションリンク用に既存の 'page' パラメータを削除
        query_params_for_pagination = current_params.copy()
        if 'page' in query_params_for_pagination:
            del query_params_for_pagination['page']
        context['current_query_params'] = query_params_for_pagination.urlencode()

        context['weekday_urls'] = {
            choice[0]: get_filtered_url(base_url, current_params, 'weekday', choice[0])
            for choice in context['form'].fields['weekday'].choices
        }
        context['tag_urls'] = {
            choice[0]: get_filtered_url(base_url, current_params, 'tags', choice[0])
            for choice in context['form'].fields['tags'].choices
        }

        # GoogleカレンダーIDを追加
        context['google_calendar_id'] = GOOGLE_CALENDAR_ID

        return context


class EventDetailPastList(ListView):
    template_name = 'event/detail_history.html'
    model = EventDetail
    context_object_name = 'event_details'
    paginate_by = 20
    RATE_LIMIT_WINDOW_SECONDS = 10 * 60
    RATE_LIMIT_MAX_REQUESTS = 20
    ALLOWED_FILTER_KEYS = ('community_name', 'speaker', 'theme')

    def _get_rate_limit_cache_key(self, client_ip):
        bucket = int(timezone.now().timestamp()) // self.RATE_LIMIT_WINDOW_SECONDS
        return f"event_detail_history:ip:{client_ip}:bucket:{bucket}"

    def _is_rate_limited(self):
        client_ip = get_client_ip(self.request)
        cache_key = self._get_rate_limit_cache_key(client_ip)
        request_count = cache.get(cache_key, 0)

        if request_count >= self.RATE_LIMIT_MAX_REQUESTS:
            return True

        if request_count == 0:
            cache.set(cache_key, 1, timeout=self.RATE_LIMIT_WINDOW_SECONDS)
        else:
            try:
                cache.incr(cache_key)
            except ValueError:
                # race condition等でキーが消えていた場合は初期化し直す
                cache.set(cache_key, 1, timeout=self.RATE_LIMIT_WINDOW_SECONDS)

        return False

    def _get_sanitized_filter_params(self):
        """検索に必要なキーのみ残し、単一値へ正規化したQueryDictを返す。"""
        params = self.request.GET.copy()

        for key in list(params.keys()):
            if key not in self.ALLOWED_FILTER_KEYS:
                del params[key]

        for key in self.ALLOWED_FILTER_KEYS:
            value = params.get(key, '').strip()
            if value:
                params.setlist(key, [value])
            elif key in params:
                del params[key]

        return params

    def dispatch(self, request, *args, **kwargs):
        if self._is_rate_limited():
            return HttpResponse(
                "アクセスが集中しています。しばらくしてから再度お試しください。",
                status=429,
                content_type='text/plain; charset=utf-8',
            )
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        # 通常のget処理の前にページ番号をチェック
        page_str = request.GET.get('page', '1')

        try:
            # ページ番号のみを抽出（数字以外を除去）
            page = int(''.join(filter(str.isdigit, page_str)) or '1')
        except (ValueError, TypeError):
            # 無効なページ番号の場合は1ページ目にリダイレクト
            params = request.GET.copy()
            params['page'] = '1'
            return redirect(f"{request.path}?{params.urlencode()}")

        self.object_list = self.get_queryset()
        paginator = self.get_paginator(self.object_list, self.paginate_by)

        if page > paginator.num_pages and paginator.num_pages > 0:
            # 存在しないページ番号の場合は1ページ目にリダイレクト
            params = request.GET.copy()
            params['page'] = '1'
            return redirect(f"{request.path}?{params.urlencode()}")

        # ページ番号が有効な場合は通常の処理を続行
        request.GET = request.GET.copy()
        request.GET['page'] = str(page)
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset().filter(
            detail_type='LT',  # LTのみ表示
            status='approved',
        ).select_related('event', 'event__community').order_by('-event__date', '-start_time')

        community_name = self.request.GET.get('community_name', '').strip()
        if community_name:
            queryset = queryset.filter(event__community__name__icontains=community_name)

        speaker = self.request.GET.get('speaker', '').strip()
        if speaker:
            queryset = queryset.filter(speaker__icontains=speaker)

        theme = self.request.GET.get('theme', '').strip()
        if theme:
            queryset = queryset.filter(theme__icontains=theme)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        query_params = self._get_sanitized_filter_params()
        context['current_query_params'] = query_params.urlencode()

        speaker_link_base_params = query_params.copy()
        if 'speaker' in speaker_link_base_params:
            del speaker_link_base_params['speaker']
        context['speaker_link_base_query'] = speaker_link_base_params.urlencode()

        community_link_base_params = query_params.copy()
        if 'community_name' in community_link_base_params:
            del community_link_base_params['community_name']
        context['community_link_base_query'] = community_link_base_params.urlencode()

        return context


class EventLogListView(ListView):
    """特別企画とブログの一覧表示"""
    template_name = 'event/event_log_list.html'
    model = EventDetail
    context_object_name = 'event_logs'
    paginate_by = 20

    def get(self, request, *args, **kwargs):
        # 通常のget処理の前にページ番号をチェック
        page_str = request.GET.get('page', '1')

        try:
            # ページ番号のみを抽出（数字以外を除去）
            page = int(''.join(filter(str.isdigit, page_str)) or '1')
        except (ValueError, TypeError):
            # 無効なページ番号の場合は1ページ目にリダイレクト
            params = request.GET.copy()
            params['page'] = '1'
            return redirect(f"{request.path}?{params.urlencode()}")

        self.object_list = self.get_queryset()
        paginator = self.get_paginator(self.object_list, self.paginate_by)

        if page > paginator.num_pages and paginator.num_pages > 0:
            # 存在しないページ番号の場合は1ページ目にリダイレクト
            params = request.GET.copy()
            params['page'] = '1'
            return redirect(f"{request.path}?{params.urlencode()}")

        # ページ番号が有効な場合は通常の処理を続行
        request.GET = request.GET.copy()
        request.GET['page'] = str(page)
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset().filter(
            detail_type__in=['SPECIAL', 'BLOG'],  # 特別企画とブログのみ表示
            status='approved',
        ).select_related('event', 'event__community').order_by('-event__date', '-start_time')

        community_name = self.request.GET.get('community_name', '').strip()
        if community_name:
            queryset = queryset.filter(event__community__name__icontains=community_name)

        theme = self.request.GET.get('theme', '').strip()
        if theme:
            queryset = queryset.filter(theme__icontains=theme)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # 現在のGETパラメータを取得
        query_params = self.request.GET.copy()
        if 'page' in query_params:
            del query_params['page']

        context['current_query_params'] = query_params.urlencode()

        return context
