import json
import logging
from urllib.parse import urlencode

from django.db.models import Count, Prefetch, Q
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import ListView, RedirectView

from event.forms import EventSearchForm
from event.models import Event, EventDetail
from event_calendar.calendar_utils import generate_google_calendar_url
from url_filters import get_filtered_url
from utils.vrchat_time import get_vrchat_today
from website.constants import CACHE_TTL_HOUR
from website.settings import GOOGLE_CALENDAR_ID

from ta_hub.utils import get_client_ip
from django.core.cache import cache
from django.http import HttpResponse, QueryDict
from django.utils import timezone

logger = logging.getLogger(__name__)

# 一覧カードのサムネイル生成幅（Cloudflare Image Resizing 用）
PRESENTATION_THUMBNAIL_WIDTH = '400'


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
    """発表一覧（ブログ風）。

    既定は「記事・動画・スライドのいずれかがある発表」のみを新しい順に並べる。
    ``?view=all`` で資料なしの発表も含め、``?type=special`` で特別企画・ブログを表示する。
    """

    template_name = 'event/detail_history.html'
    model = EventDetail
    context_object_name = 'event_details'
    paginate_by = 20
    RATE_LIMIT_WINDOW_SECONDS = 10 * 60
    RATE_LIMIT_MAX_REQUESTS = 60
    ALLOWED_FILTER_KEYS = ('community_name', 'speaker', 'theme', 'q', 'view', 'type')
    # 単独の絞り込みチップとして「× で外せる」形で表示するキー
    REMOVABLE_FILTER_KEYS = (
        ('q', 'キーワード'),
        ('theme', 'テーマ'),
        ('community_name', '集会'),
        ('speaker', '発表者'),
    )
    SPECIAL_TYPES = ('SPECIAL', 'BLOG')
    VIEW_ALL = 'all'
    TYPE_SPECIAL = 'special'
    SUMMARY_CACHE_KEY = 'presentation_list_summary'
    PAGE_TITLE_LT = 'VRChat 技術・学術系 発表一覧'
    PAGE_TITLE_SPECIAL = 'VRChat 技術・学術系 特別企画・ブログ一覧'
    META_DESCRIPTION_SPECIAL = (
        'VRChatの技術・学術系集会が企画した特別イベントと、運営・主催者によるブログ記事の一覧です。'
        '新しい順に掲載。集会名やキーワードで探せます。'
    )

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

        # 列挙値は既知の値以外を捨てる（任意文字列をURLに残さない）
        if params.get('view') != self.VIEW_ALL and 'view' in params:
            del params['view']
        if params.get('type') != self.TYPE_SPECIAL and 'type' in params:
            del params['type']

        return params

    @property
    def show_all(self):
        return self.request.GET.get('view', '').strip() == self.VIEW_ALL

    @property
    def is_special(self):
        return self.request.GET.get('type', '').strip() == self.TYPE_SPECIAL

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

    def _apply_filters(self, queryset):
        """GET パラメータによる絞り込みを適用する。"""
        community_name = self.request.GET.get('community_name', '').strip()
        if community_name:
            queryset = queryset.filter(event__community__name__icontains=community_name)

        speaker = self.request.GET.get('speaker', '').strip()
        if speaker:
            queryset = queryset.filter(speaker__icontains=speaker)

        # theme は旧URL互換。q と同じ横断検索として扱う
        keyword = self.request.GET.get('q', '').strip() or self.request.GET.get('theme', '').strip()
        if keyword:
            queryset = queryset.filter(
                Q(theme__icontains=keyword)
                | Q(h1__icontains=keyword)
                | Q(speaker__icontains=keyword)
                | Q(event__community__name__icontains=keyword)
            )

        return queryset

    def get_queryset(self):
        queryset = super().get_queryset().filter(
            status='approved',
            event__community__status='approved',
        )
        if self.is_special:
            queryset = queryset.filter(detail_type__in=self.SPECIAL_TYPES)
        else:
            queryset = queryset.filter(detail_type='LT')

        queryset = self._apply_filters(queryset)
        # 件数チップ用に「表示切替を適用する前」の queryset を保持する
        self._filtered_base = queryset

        if not self.show_all:
            queryset = queryset.filter(EventDetail.materials_q())

        return queryset.select_related(
            'event', 'event__community'
        ).order_by('-event__date', '-start_time')

    def _build_summary(self):
        """サイト全体の活動サマリー（集会数・発表数・発表者数）を1時間キャッシュで返す。"""
        summary = cache.get(self.SUMMARY_CACHE_KEY)
        if summary is not None:
            return summary

        base = EventDetail.objects.filter(
            detail_type='LT',
            status='approved',
            event__community__status='approved',
        )
        aggregated = base.aggregate(
            community_count=Count('event__community', distinct=True),
            presentation_count=Count('id', distinct=True),
        )
        summary = {
            'community_count': aggregated['community_count'],
            'presentation_count': aggregated['presentation_count'],
            'speaker_count': base.exclude(speaker='').values('speaker').distinct().count(),
        }
        cache.set(self.SUMMARY_CACHE_KEY, summary, CACHE_TTL_HOUR)
        return summary

    def _chip_url(self, params, key, value):
        """``params`` の ``key`` を差し替え（``value`` が None なら削除）た URL を返す。"""
        chip_params = params.copy()
        if value is None:
            if key in chip_params:
                del chip_params[key]
        else:
            chip_params.setlist(key, [value])
        encoded = chip_params.urlencode()
        return f"{self.request.path}?{encoded}" if encoded else self.request.path

    def _build_filter_chips(self, params):
        """適用中の絞り込みを「× で外せる」チップ用のリストにして返す。"""
        chips = []
        for key, label in self.REMOVABLE_FILTER_KEYS:
            value = params.get(key, '')
            if value:
                chips.append({
                    'label': label,
                    'value': value,
                    'remove_url': self._chip_url(params, key, None),
                })
        return chips

    def _thumbnail_url(self, detail):
        """構造化データ用に、カードと同じ4段フォールバックで画像の絶対URLを返す。"""
        if detail.thumbnail_image:
            return self.request.build_absolute_uri(detail.thumbnail_image.url)
        if detail.video_id:
            return f"https://img.youtube.com/vi/{detail.video_id}/mqdefault.jpg"
        poster = detail.event.community.poster_image
        if poster:
            return self.request.build_absolute_uri(poster.url)
        return ''

    @property
    def page_title(self):
        return self.PAGE_TITLE_SPECIAL if self.is_special else self.PAGE_TITLE_LT

    def _canonical_path(self):
        """canonical / og:url 用のパス。種別は別ページとして宣言し、絞り込み・ページ番号は集約する。"""
        path = reverse('event:detail_history')
        if self.is_special:
            return f"{path}?{urlencode({'type': self.TYPE_SPECIAL})}"
        return path

    def _build_structured_data(self, details):
        """BreadcrumbList と CollectionPage(ItemList) の JSON 文字列を返す。"""
        request = self.request
        home_url = request.build_absolute_uri('/')
        list_url = request.build_absolute_uri(self._canonical_path())
        list_name = '特別企画・ブログ一覧' if self.is_special else '発表一覧'

        breadcrumbs = {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "ホーム", "item": home_url},
                {"@type": "ListItem", "position": 2, "name": list_name, "item": list_url},
            ],
        }

        items = []
        for idx, detail in enumerate(details, start=1):
            item = {
                "@type": "ListItem",
                "position": idx,
                "name": detail.title,
                "url": request.build_absolute_uri(
                    reverse('event:detail', kwargs={'pk': detail.pk})
                ),
            }
            thumbnail = self._thumbnail_url(detail)
            if thumbnail:
                item["image"] = thumbnail
            items.append(item)

        collection = {
            "@context": "https://schema.org",
            "@type": "CollectionPage",
            "name": list_name,
            "url": list_url,
            "inLanguage": "ja-JP",
            "isPartOf": home_url,
            "mainEntity": {"@type": "ItemList", "itemListElement": items},
        }
        # <script> 内に埋め込むので、HTML として解釈されうる文字を JSON エスケープに置き換える
        # （django.utils.html.json_script と同じ方針。</script> と <!-- の両方を封じる）
        return (
            json.dumps([breadcrumbs, collection], ensure_ascii=False)
            .replace('<', '\\u003c')
            .replace('>', '\\u003e')
            .replace('&', '\\u0026')
        )

    def _build_link_base_queries(self, context, query_params):
        """カード内の集会名・発表者リンク用に、当該キーを除いたクエリ文字列を詰める。"""
        speaker_params = query_params.copy()
        if 'speaker' in speaker_params:
            del speaker_params['speaker']
        context['speaker_link_base_query'] = speaker_params.urlencode()

        community_params = query_params.copy()
        if 'community_name' in community_params:
            del community_params['community_name']
        context['community_link_base_query'] = community_params.urlencode()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        query_params = self._get_sanitized_filter_params()
        context['current_query_params'] = query_params.urlencode()
        self._build_link_base_queries(context, query_params)

        context['show_all'] = self.show_all
        context['is_special'] = self.is_special
        context['keyword'] = (
            self.request.GET.get('q', '').strip()
            or self.request.GET.get('theme', '').strip()
        )
        context['summary'] = self._build_summary()
        context['materials_count'] = self._filtered_base.filter(EventDetail.materials_q()).count()
        context['all_count'] = self._filtered_base.count()
        context['filter_chips'] = self._build_filter_chips(query_params)
        context['view_materials_url'] = self._chip_url(query_params, 'view', None)
        context['view_all_url'] = self._chip_url(query_params, 'view', self.VIEW_ALL)
        context['type_lt_url'] = self._chip_url(query_params, 'type', None)
        context['type_special_url'] = self._chip_url(query_params, 'type', self.TYPE_SPECIAL)
        # 「絞り込みを解除」は検索条件だけ外し、表示モード（種別）は維持する
        base_params = QueryDict(mutable=True)
        if self.is_special:
            base_params['type'] = self.TYPE_SPECIAL
        context['clear_filters_url'] = self._chip_url(base_params, 'view', None)
        context['page_title'] = self.page_title
        context['canonical_path'] = self._canonical_path()
        context['meta_description'] = (
            self.META_DESCRIPTION_SPECIAL if self.is_special
            else self._build_meta_description(context['summary'])
        )

        try:
            context['structured_data_json'] = self._build_structured_data(
                context.get('object_list', [])
            )
        except Exception as exc:  # 構造化データの失敗でページを落とさない
            logger.warning("Failed to prepare structured data for presentation list: %s", exc)

        return context

    def _build_meta_description(self, summary):
        return (
            f"VRChatの技術・学術系集会で行われた発表{summary['presentation_count']}件"
            f"（{summary['community_count']}集会・{summary['speaker_count']}人の発表者）の一覧です。"
            "動画・スライド・記事のある発表を新しい順に掲載。集会名や発表者、キーワードで探せます。"
        )


class EventLogRedirectView(RedirectView):
    """旧「特別企画・ブログ一覧」を発表一覧の ``?type=special`` へ恒久リダイレクトする。"""

    permanent = True

    def get_redirect_url(self, *args, **kwargs):
        params = {'type': EventDetailPastList.TYPE_SPECIAL}
        # 旧URLの絞り込み（集会名・テーマ）は新一覧でも同じキーで有効なので引き継ぐ
        for key in ('community_name', 'theme'):
            value = self.request.GET.get(key, '').strip()
            if value:
                params[key] = value
        return f"{reverse('event:detail_history')}?{urlencode(params)}"
