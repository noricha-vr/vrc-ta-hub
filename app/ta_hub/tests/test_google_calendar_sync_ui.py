"""Googleカレンダー連携UIのテスト"""

from django.conf import settings
from django.test import TestCase, Client
from django.urls import reverse


class GoogleCalendarSyncUITest(TestCase):
    """トップページのGoogleカレンダー連携UIのテスト"""

    def setUp(self):
        self.client = Client()

    def test_google_calendar_id_in_context(self):
        """google_calendar_idがコンテキストに含まれること"""
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        self.assertIn('google_calendar_id', response.context)
        self.assertEqual(response.context['google_calendar_id'], settings.GOOGLE_CALENDAR_ID)

    def test_calendar_sync_section_displayed(self):
        """Googleカレンダー連携セクションが表示されること"""
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Googleカレンダーと連携して予定を管理')
        self.assertContains(response, 'bi-calendar-plus')

    def test_calendar_sync_collapse_structure(self):
        """折りたたみ構造（Bootstrap collapse）が正しいこと"""
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        # collapseのトリガー
        self.assertContains(response, 'data-bs-toggle="collapse"')
        self.assertContains(response, 'href="#calendarSyncDetails"')
        # collapseのターゲット
        self.assertContains(response, 'id="calendarSyncDetails"')
        self.assertContains(response, 'class="collapse"')

    def test_toggle_text_elements_present(self):
        """トグルテキスト要素が存在すること"""
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'toggle-open')
        self.assertContains(response, 'toggle-close')
        self.assertContains(response, 'bi-chevron-down')
        self.assertContains(response, 'bi-chevron-up')

    def test_full_sync_option_displayed(self):
        """全イベント同期オプションが表示されること"""
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '全イベントを同期')
        self.assertContains(response, '一度追加すれば、新しいイベントも自動で反映されます')
        self.assertContains(response, 'bi-arrow-repeat')

    def test_individual_add_option_displayed(self):
        """個別追加オプションが表示されること"""
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '気になるイベントだけ追加')
        self.assertContains(response, 'bi-star')

    def test_google_calendar_link_correct(self):
        """Googleカレンダー追加リンクが正しいこと"""
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        # calendar.google.comへのリンクがあること
        self.assertContains(response, 'https://calendar.google.com/calendar/u/0/r?cid=')
        # Googleカレンダーに追加ボタン
        self.assertContains(response, 'Googleカレンダーに追加')
        self.assertContains(response, 'bi-google')

    def test_toggle_javascript_present(self):
        """トグル切り替え用JavaScriptが存在すること"""
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'calendarSyncDetails')
        self.assertContains(response, 'show.bs.collapse')
        self.assertContains(response, 'hide.bs.collapse')
