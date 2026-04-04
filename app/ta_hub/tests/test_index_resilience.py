from unittest.mock import patch

from django.core.cache import cache
from django.db import OperationalError
from django.test import RequestFactory, TestCase
from django.contrib.auth.models import AnonymousUser

from ta_hub.views import IndexView, VKET_ACHIEVEMENTS
from utils.vrchat_time import get_vrchat_today


class IndexViewResilienceTest(TestCase):
    """トップページのDB障害時フェイルセーフのテスト"""

    def setUp(self):
        self.factory = RequestFactory()
        cache.clear()
        self.cache_key = f'index_view_data_{get_vrchat_today()}'

    def tearDown(self):
        cache.clear()

    def _build_view(self):
        request = self.factory.get('/')
        request.user = AnonymousUser()

        view = IndexView()
        view.setup(request)
        return view

    @patch('ta_hub.views.Post.objects.filter', side_effect=OperationalError('db unavailable'))
    def test_index_uses_cache_before_hitting_database(self, mocked_post_filter):
        """キャッシュヒット時はDB障害があってもトップページを返す"""
        cached_data = {
            'vket_achievements': [{
                'id': 'cached',
                'title': 'cached',
                'period': '',
                'stats': {},
                'hashtags': [],
                'news_slug': 'cached',
                'image': 'cached-image',
            }],
            'upcoming_events': [{'id': 1}],
            'upcoming_event_details': [{'id': 2}],
            'special_events': [{'id': 3}],
        }
        cache.set(self.cache_key, cached_data, 60)

        context = self._build_view().get_context_data()

        self.assertEqual(context['vket_achievements'], cached_data['vket_achievements'])
        self.assertEqual(context['upcoming_events'], cached_data['upcoming_events'])
        self.assertEqual(context['upcoming_event_details'], cached_data['upcoming_event_details'])
        self.assertEqual(context['special_events'], cached_data['special_events'])
        mocked_post_filter.assert_not_called()

    @patch('ta_hub.views.Post.objects.filter', side_effect=OperationalError('db unavailable'))
    def test_index_returns_empty_sections_when_database_is_unavailable(self, mocked_post_filter):
        """キャッシュミス時にDB接続へ失敗しても空状態で200を返す"""
        context = self._build_view().get_context_data()

        self.assertEqual(context['upcoming_events'], [])
        self.assertEqual(context['upcoming_event_details'], [])
        self.assertEqual(context['special_events'], [])
        self.assertEqual(len(context['vket_achievements']), len(VKET_ACHIEVEMENTS))
        self.assertTrue(
            all('image' in achievement for achievement in context['vket_achievements'])
        )
        self.assertTrue(
            all(achievement['image'] is None for achievement in context['vket_achievements'])
        )
        mocked_post_filter.assert_called_once()
