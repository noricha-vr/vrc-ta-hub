"""VKETコラボ実績セクションのテスト"""

from django.test import TestCase, Client
from django.urls import reverse

from ta_hub.views import VKET_ACHIEVEMENTS


class VketAchievementsConstantTest(TestCase):
    """VKET_ACHIEVEMENTS定数のテスト"""

    def test_vket_achievements_is_list(self):
        """VKET_ACHIEVEMENTSがリストであること"""
        self.assertIsInstance(VKET_ACHIEVEMENTS, list)

    def test_vket_achievements_has_required_fields(self):
        """各実績に必須フィールドがあること"""
        required_fields = ['id', 'title', 'period', 'stats', 'image', 'hashtags', 'news_slug']

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
        self.assertEqual(response.context['vket_achievements'], VKET_ACHIEVEMENTS)

    def test_vket_achievements_section_displayed(self):
        """VKETコラボ実績セクションが表示されること"""
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'VKETコラボ実績')
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
