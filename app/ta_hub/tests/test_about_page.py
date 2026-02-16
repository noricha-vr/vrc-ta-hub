"""Aboutページのスタッフリンク表示テスト"""

from django.test import TestCase, Client, override_settings
from django.urls import reverse


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class AboutPageStaffLinksTest(TestCase):
    """Aboutページのスタッフ情報表示テスト"""

    def setUp(self):
        self.client = Client()

    def test_about_page_contains_updated_staff_links_and_image(self):
        """スタッフリンクと画像パスが更新されていること"""
        response = self.client.get(reverse('ta_hub:about'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'https://x.com/kimkim0106_3218')
        self.assertContains(response, 'https://x.com/yuni_shinogami')
        self.assertContains(response, 'https://x.com/MadaoVeryPoor')
        self.assertContains(response, '/static/ta_hub/images/staff/yuni_shinogami_2026.jpg')
        self.assertContains(response, 'https://pbs.twimg.com/profile_images/1984045911878905856/7AIZXUqz_400x400.jpg')
        self.assertNotContains(response, 'href="#" class="text-primary" target="_blank"')
