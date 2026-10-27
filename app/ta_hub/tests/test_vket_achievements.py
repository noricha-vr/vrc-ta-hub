"""VKETコラボ実績セクションのテスト"""

from datetime import date, datetime, timezone
from unittest.mock import patch

from bs4 import BeautifulSoup
from django.core.cache import cache
from django.db.utils import OperationalError
from django.test import TestCase, Client, RequestFactory
from django.urls import reverse

from news.models import Category, Post
from ta_hub.views import IndexView, VKET_ACHIEVEMENTS
from tests.factories import make_community, make_event
from vket.models import VketCollaboration, VketParticipation


class VketAchievementsConstantTest(TestCase):
    """VKET_ACHIEVEMENTS定数のテスト"""

    def test_vket_achievements_is_list(self):
        """VKET_ACHIEVEMENTSがリストであること"""
        self.assertIsInstance(VKET_ACHIEVEMENTS, list)

    def test_vket_achievements_has_required_fields(self):
        """各実績に必須フィールドがあること"""
        # imageはニュース記事のサムネイルから動的に取得するため、定数には含まれない
        required_fields = ['id', 'title', 'period', 'stats', 'hashtags', 'news_slug']

        for achievement in VKET_ACHIEVEMENTS:
            for field in required_fields:
                self.assertIn(field, achievement, f"Achievement missing field: {field}")

    def test_vket_achievements_stats_has_required_fields(self):
        """stats辞書に必須フィールドがあること"""
        for achievement in VKET_ACHIEVEMENTS:
            self.assertIn('days', achievement['stats'])
            self.assertIn('communities', achievement['stats'])


class VketAchievementsSectionTest(TestCase):
    """VKETコラボ実績セクション表示のテスト"""

    def setUp(self):
        self.client = Client()

    def test_vket_achievements_in_context(self):
        """vket_achievementsがコンテキストに含まれること"""
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        self.assertIn('vket_achievements', response.context)
        # imageはニュース記事のサムネイルから動的に取得されるため、フィールドの存在を確認
        context_achievements = response.context['vket_achievements']
        self.assertEqual(len(context_achievements), len(VKET_ACHIEVEMENTS))
        for i, achievement in enumerate(context_achievements):
            self.assertEqual(achievement['id'], VKET_ACHIEVEMENTS[i]['id'])
            self.assertEqual(achievement['title'], VKET_ACHIEVEMENTS[i]['title'])
            self.assertIn('image', achievement)  # imageフィールドが動的に追加されていること

    def test_vket_achievements_section_displayed(self):
        """VKETコラボセクションが表示されること"""
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'VKETコラボ')
        self.assertContains(response, 'bi-trophy-fill')

    def test_vket_achievement_titles_displayed(self):
        """各実績のタイトルが表示されること"""
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        for achievement in VKET_ACHIEVEMENTS:
            self.assertContains(response, achievement['title'])

    def test_vket_achievement_periods_displayed(self):
        """各実績の開催期間が表示されること"""
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        for achievement in VKET_ACHIEVEMENTS:
            self.assertContains(response, achievement['period'])

    def test_vket_achievement_links_to_news(self):
        """各実績がニュース詳細ページにリンクしていること"""
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        for achievement in VKET_ACHIEVEMENTS:
            expected_url = reverse('news:detail', args=[achievement['news_slug']])
            self.assertContains(response, expected_url)

    def test_activity_history_button_displayed(self):
        """活動履歴をもっと見るボタンが表示されること"""
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '活動履歴をもっと見る')
        activity_url = reverse('news:category_list', args=['activity'])
        self.assertContains(response, activity_url)


class VketAchievementsDatabaseTest(TestCase):
    """会期後のアーカイブから作る実績とDB障害時のフォールバック。"""

    @classmethod
    def setUpTestData(cls):
        cls.category, _ = Category.objects.get_or_create(slug='activity', defaults={'name': '活動履歴'})

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        today_patch = patch('ta_hub.views.get_vrchat_today', return_value=date(2026, 10, 27))
        today_patch.start()
        self.addCleanup(today_patch.stop)
        self.view = IndexView()
        self.view.setup(RequestFactory().get('/'))

    def _post(self, slug='vket-2026-summer', **extra):
        defaults = {
            'title': 'コラボのアーカイブ', 'category': self.category,
            'body_markdown': '発表動画まとめ', 'is_published': True,
            'thumbnail': 'news/vket-archive.jpg',
        }
        defaults.update(extra)
        return Post.objects.create(slug=slug, **defaults)

    def _collaboration(self, slug='2026-summer', **extra):
        defaults = {
            'name': 'Vket 2026 Summer 技術学術WEEK',
            'phase': VketCollaboration.Phase.ARCHIVED,
            'period_start': date(2026, 7, 11), 'period_end': date(2026, 7, 26),
            'registration_deadline': date(2026, 6, 1), 'lt_deadline': date(2026, 6, 20),
            'hashtags': ['#Vketステージ', '#技術学術WEEK2026'],
            'settings_json': {'archive_news_slug': 'vket-2026-summer'},
        }
        defaults.update(extra)
        return VketCollaboration.objects.create(slug=slug, **defaults)

    def _participation(
        self, collaboration, status='approved', lifecycle=VketParticipation.Lifecycle.ACTIVE, published=True,
    ):
        community = make_community(status=status)
        event = make_event(community, event_date=collaboration.period_start) if published else None
        return VketParticipation.objects.create(
            collaboration=collaboration, community=community, lifecycle=lifecycle, published_event=event,
        )

    def test_completed_collaboration_card_precedes_legacy_and_renders_archive(self):
        post = self._post()
        collaboration = self._collaboration()
        self._participation(collaboration)
        self._participation(collaboration)

        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        achievements = response.context['vket_achievements']
        image = post.get_absolute_thumbnail_url(response.wsgi_request)
        self.assertEqual(achievements[0], {
            'id': collaboration.slug,
            'title': '🌻 Vket 2026 Summer 技術学術WEEK',
            'period': '2026年7月11日〜7月26日',
            'stats': {'days': 16, 'communities': 2},
            'hashtags': collaboration.hashtags,
            'news_slug': post.slug,
            'image': image,
        })
        self.assertEqual([a['id'] for a in achievements[1:]], [a['id'] for a in VKET_ACHIEVEMENTS])
        soup = BeautifulSoup(response.content, 'html.parser')
        card = soup.select_one('.vket-achievement-card')
        self.assertEqual(card.parent['href'], reverse('news:detail', args=[post.slug]))
        self.assertEqual(card.select_one('img')['src'], image)
        for text in [achievements[0]['title'], achievements[0]['period'], '16日間', '2団体参加', *collaboration.hashtags]:
            self.assertIn(text, card.get_text())

    def test_ineligible_collaborations_are_hidden(self):
        self._post()
        self._post('private-archive', is_published=False)
        cases = [
            {'phase': VketCollaboration.Phase.DRAFT},
            {'period_start': date(2026, 10, 20), 'period_end': date(2026, 11, 1)},
            {'period_start': date(2026, 11, 1), 'period_end': date(2026, 11, 15)},
            {'period_end': date(2026, 10, 27)},
            {'settings_json': {'archive_news_slug': 'private-archive'}},
            {'settings_json': {'archive_news_slug': 'missing-archive'}},
        ]
        for index, extra in enumerate(cases):
            self._collaboration(slug=f'excluded-{index}', **extra)

        achievements = self.view._build_vket_achievements(with_images=True)

        self.assertEqual([a['id'] for a in achievements], [a['id'] for a in VKET_ACHIEVEMENTS])

    def test_missing_or_invalid_archive_settings_are_hidden(self):
        self._post()
        settings_values = [None, [], 'invalid', {}, *[
            {'archive_news_slug': value} for value in ['', None, 42, True, [], {}]
        ]]
        for index, value in enumerate(settings_values):
            self._collaboration(slug=f'invalid-{index}', settings_json=value)

        achievements = self.view._build_vket_achievements(with_images=True)

        self.assertEqual(len(achievements), len(VKET_ACHIEVEMENTS))

    def test_only_active_approved_communities_are_counted(self):
        self._post()
        collaboration = self._collaboration()
        self._participation(collaboration)
        for status in ['pending', 'rejected', 'closed']:
            self._participation(collaboration, status=status)
        for lifecycle in [VketParticipation.Lifecycle.DECLINED, VketParticipation.Lifecycle.WITHDRAWN]:
            self._participation(collaboration, lifecycle=lifecycle)
        # 参加登録だけで開催イベントが公開されていない集会は数えない。
        self._participation(collaboration, published=False)

        achievements = self.view._build_vket_achievements(with_images=True)

        self.assertEqual(achievements[0]['stats']['communities'], 1)

    def test_newer_collaborations_come_first_and_cross_year_period_includes_year(self):
        self._post()
        self._collaboration()
        self._post('vket-winter')
        winter = self._collaboration(
            slug='2025-winter', name='冬のコラボ',
            period_start=date(2025, 12, 27), period_end=date(2026, 1, 4),
            settings_json={'archive_news_slug': 'vket-winter'},
        )

        achievements = self.view._build_vket_achievements(with_images=True)

        self.assertEqual([a['id'] for a in achievements[:2]], ['2026-summer', winter.slug])
        self.assertEqual(achievements[1]['title'], '❄️ 冬のコラボ')
        self.assertEqual(achievements[1]['period'], '2025年12月27日〜2026年1月4日')
        self.assertEqual(achievements[1]['stats'], {'days': 9, 'communities': 0})

    def test_theme_matches_top_banner_with_month_fallback(self):
        self._post()
        collaboration = self._collaboration()
        for banner, emoji in [
            ({'theme': 'winter'}, '❄️'), ({'theme': 'summer'}, '🌻'),
            ({'theme': 'invalid'}, '🌻'), (None, '🌻'), ('invalid', '🌻'),
        ]:
            with self.subTest(banner=banner):
                collaboration.settings_json = {'archive_news_slug': 'vket-2026-summer', 'top_banner': banner}
                collaboration.save(update_fields=['settings_json'])
                achievement = self.view._build_vket_achievements(with_images=True)[0]
                self.assertEqual(achievement['title'], f'{emoji} {collaboration.name}')

    def test_automatic_card_replaces_legacy_card_with_same_archive(self):
        legacy_slug = VKET_ACHIEVEMENTS[0]['news_slug']
        self._post(legacy_slug)
        collaboration = self._collaboration(settings_json={'archive_news_slug': legacy_slug})

        achievements = self.view._build_vket_achievements(with_images=True)

        self.assertEqual(len(achievements), 2)
        self.assertEqual(achievements[0]['id'], collaboration.slug)
        self.assertEqual([a['news_slug'] for a in achievements], [a['news_slug'] for a in VKET_ACHIEVEMENTS])

    def test_query_count_stays_two_as_collaborations_and_participations_grow(self):
        with self.assertNumQueries(2):
            self.view._build_vket_achievements(with_images=True)
        self._post(thumbnail='')
        collaboration = self._collaboration()
        self._participation(collaboration)
        with self.assertNumQueries(2):
            achievements = self.view._build_vket_achievements(with_images=True)
        self.assertEqual(achievements[0]['stats']['communities'], 1)
        for index in range(5):
            slug = f'archive-{index}'
            self._post(slug, thumbnail='')
            collaboration = self._collaboration(slug=slug, settings_json={'archive_news_slug': slug})
            for _ in range(4):
                self._participation(collaboration)
        with self.assertNumQueries(2):
            achievements = self.view._build_vket_achievements(with_images=True)
        self.assertEqual(len(achievements), 8)
        self.assertEqual([a['stats']['communities'] for a in achievements[:6]], [4] * 5 + [1])

    def test_legacy_card_hides_thumbnail_of_unpublished_article(self):
        legacy_slug = VKET_ACHIEVEMENTS[0]['news_slug']
        self._post(legacy_slug, is_published=False)

        achievements = self.view._build_vket_achievements(with_images=True)

        legacy = next(a for a in achievements if a['news_slug'] == legacy_slug)
        self.assertIsNone(legacy['image'])

    def test_without_images_returns_only_legacy_cards_without_queries(self):
        self._post()
        self._collaboration()

        with self.assertNumQueries(0):
            achievements = self.view._build_vket_achievements(with_images=False)

        self.assertEqual(achievements, [dict(a, image=None) for a in VKET_ACHIEVEMENTS])
        self.assertTrue(all('image' not in a for a in VKET_ACHIEVEMENTS))

    @patch('ta_hub.views.build_index_database_context', return_value={})
    @patch('ta_hub.views.VketCollaboration.objects.exclude', side_effect=OperationalError('db unavailable'))
    def test_database_failure_keeps_legacy_fallback(self, _mock_collaborations, _mock_context):
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['database_degraded'])
        self.assertEqual(response.context['vket_achievements'], [dict(a, image=None) for a in VKET_ACHIEVEMENTS])


class VketNoticeLayoutTest(TestCase):
    """Vket告知見出しの装飾配置テスト"""

    def setUp(self):
        from vket.models import VketCollaboration

        VketCollaboration.objects.create(
            slug='summer-layout', name='夏の技術学術WEEK', phase='locked',
            period_start='2026-07-11', period_end='2026-07-25',
            registration_deadline='2026-06-01', lt_deadline='2026-06-20',
            settings_json={'top_banner': {'start': '2026-07-01T20:00:00+09:00'}},
        )

    @patch(
        'ta_hub.views.timezone.now',
        return_value=datetime(2026, 7, 20, tzinfo=timezone.utc),
    )
    def test_sunflowers_use_three_column_heading_layout(self, _mock_now):
        """ひまわりを見出しの上下ではなく左右に固定する"""
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="vket-notice-heading mb-2"')
        self.assertContains(response, 'display: inline-grid;')
        self.assertContains(response, 'grid-template-columns: auto minmax(0, auto) auto;')
        self.assertContains(response, 'class="vket-notice-sunflower"', count=2)
