import logging

from django.conf import settings
from django.db.utils import OperationalError
from django.utils import timezone
from django.views.generic import TemplateView
from django.views.static import serve

from news.models import Post
from ta_hub.index_cache import (
    build_index_database_context,
    get_index_view_cache_key,
)
from utils.vrchat_time import get_vrchat_today

logger = logging.getLogger(__name__)

# VKETコラボデータ（画像はニュース記事のサムネイルから取得）
VKET_ACHIEVEMENTS = [
    {
        'id': 'winter-2025',
        'title': '❄️ Vket 2025 Winter 技術学術WEEK',
        'period': '2025年12月6日〜12月21日',
        'stats': {'days': 16, 'communities': 20},
        'hashtags': ['#Vketステージ', '#Vket技術学術WEEK'],
        'news_slug': 'vket-2025-winter',
    },
    {
        'id': 'summer-2025',
        'title': '🌻 Vket 2025 Summer 技術学術WEEK',
        'period': '2025年7月12日〜7月27日',
        'stats': {'days': 16, 'communities': 20},
        'hashtags': ['#Vketステージ', '#Vket技術学術WEEK'],
        'news_slug': 'vket-2025-summer',
    },
]


class IndexView(TemplateView):
    template_name = 'ta_hub/index.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # キャッシュキーの生成（日付ベース）
        # VRChatterの生活リズムに合わせて朝4時を日付の境界とする
        today = get_vrchat_today()
        cache_key = get_index_view_cache_key(today)

        # Vketコラボ告知の表示判定
        current_datetime = timezone.now()
        vket_start_datetime = timezone.datetime(2026, 7, 11, 0, 0, tzinfo=timezone.get_current_timezone())
        vket_end_datetime = timezone.datetime(2026, 7, 26, 0, 0, tzinfo=timezone.get_current_timezone())
        context['show_vket_notice'] = current_datetime < vket_end_datetime
        context['vket_start_date'] = vket_start_datetime.date()
        context['vket_end_date'] = vket_end_datetime.date()
        context['current_date'] = timezone.localdate()
        context['google_calendar_id'] = settings.GOOGLE_CALENDAR_ID
        logger.info(f"Vket notice visibility: {context['show_vket_notice']} (current: {current_datetime})")

        context['database_degraded'] = False
        # vket_achievements はDB障害時に備えて画像なしで初期化する
        context['vket_achievements'] = self._build_vket_achievements(with_images=False)
        context['upcoming_events'] = []
        context['upcoming_event_details'] = []
        context['special_events'] = []

        try:
            context.update(build_index_database_context(self.request, today, cache_key))
            # vket_achievements は request.build_absolute_uri() に依存するためキャッシュ外で毎回生成する。
            context['vket_achievements'] = self._build_vket_achievements(with_images=True)
        except OperationalError as exc:
            # トップページはRDS瞬断でも静的導線を返し続ける（公開導線だけは維持する判断）。
            # 既知の縮退経路なので error_reporting の例外通知対象にしない。
            logger.warning(
                "IndexView degraded gracefully because the database was unavailable: %s",
                exc,
            )
            context['database_degraded'] = True

        return context

    def _build_vket_achievements(self, with_images):
        """vket_achievements リストを生成する。

        with_images=True のとき Post.objects.filter でサムネイルを取得して付加する。
        with_images=False のとき image=None でフォールバックリストを返す（DB障害時用）。
        request.build_absolute_uri() に依存するためキャッシュ外で毎回生成すること。
        """
        if with_images:
            news_slugs = [a['news_slug'] for a in VKET_ACHIEVEMENTS]
            news_posts = Post.objects.filter(slug__in=news_slugs).only('slug', 'thumbnail')
            thumbnail_map = {post.slug: post.get_absolute_thumbnail_url(self.request) for post in news_posts}
        else:
            thumbnail_map = {}

        result = []
        for achievement in VKET_ACHIEVEMENTS:
            achievement_copy = achievement.copy()
            achievement_copy['image'] = thumbnail_map.get(achievement['news_slug'])
            result.append(achievement_copy)
        return result


def favicon_view(request):
    return serve(request, 'favicon.ico', document_root='site')


def apple_touch_icon_view(request):
    return serve(request, 'apple-touch-icon.png', document_root='site')
