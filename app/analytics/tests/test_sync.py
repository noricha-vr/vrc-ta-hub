from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from community.models import Community

from analytics.models import PageAnalytics

TEST_TOKEN = 'test-request-token'


@override_settings(REQUEST_TOKEN=TEST_TOKEN, GA4_PROPERTY_ID='123456789')
class SyncAnalyticsViewTest(TestCase):
    """sync_analytics view のテスト。

    setUp で fetch_poster_click_report を mock 化（CI で実 GA4 へ gRPC 接続させない）。
    """
    @classmethod
    def setUpTestData(cls):
        cls.community = Community.objects.create(
            name='テスト集会',
            frequency='毎週',
            organizers='主催A',
        )

    def setUp(self):
        self.url = reverse('analytics:sync')
        self.community_path = f'/community/{self.community.pk}/'
        # poster_click 取得は本テスト範囲外。実 GA4 への gRPC 接続を避けるため mock
        poster_patcher = patch(
            'analytics.views.fetch_poster_click_report', return_value=[],
        )
        self.addCleanup(poster_patcher.stop)
        poster_patcher.start()

    def _rows(self, pv=10):
        """GA4 から返る想定の行（community 紐付き1件 + 紐付かない1件）。"""
        return [
            {
                'page_path': self.community_path,
                'date': '2026-05-31',
                'source_medium': 'google / organic',
                'pv': pv,
                'users': pv - 2,
                'sessions': pv - 1,
            },
            {
                'page_path': '/about/',  # 紐付かない → GLOBAL レコードとして保存される
                'date': '2026-05-31',
                'source_medium': '(direct) / (none)',
                'pv': 99,
                'users': 99,
                'sessions': 99,
            },
        ]

    @patch('analytics.views.fetch_page_report')
    def test_idempotent_sync_overwrites_without_duplicating(self, mock_fetch):
        mock_fetch.return_value = self._rows(pv=10)
        res1 = self.client.get(self.url, HTTP_REQUEST_TOKEN=TEST_TOKEN)
        self.assertEqual(res1.status_code, 200)
        # community 紐付き 1 件 + GLOBAL 1 件 = 計 2 件
        self.assertEqual(PageAnalytics.objects.count(), 2)

        # 同じキーで pv だけ変えて再 sync → 行は増えず値が上書き
        mock_fetch.return_value = self._rows(pv=25)
        res2 = self.client.get(self.url, HTTP_REQUEST_TOKEN=TEST_TOKEN)
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(PageAnalytics.objects.count(), 2)
        record = PageAnalytics.objects.get(page_path=self.community_path)
        self.assertEqual(record.pv, 25)
        self.assertEqual(record.users, 23)
        self.assertEqual(record.sessions, 24)

    @patch('analytics.views.fetch_page_report')
    def test_unrelated_path_is_saved_as_global(self, mock_fetch):
        """community/event_detail に紐付かない URL は GLOBAL レコードとして保存される。"""
        mock_fetch.return_value = self._rows()
        res = self.client.get(self.url, HTTP_REQUEST_TOKEN=TEST_TOKEN)
        self.assertEqual(res.status_code, 200)
        about = PageAnalytics.objects.get(page_path='/about/')
        self.assertEqual(about.content_type, PageAnalytics.ContentType.GLOBAL)
        self.assertIsNone(about.community)
        self.assertEqual(about.object_id, 0)
        self.assertEqual(about.pv, 99)

    @patch('analytics.views.fetch_page_report')
    def test_resolved_record_fields(self, mock_fetch):
        mock_fetch.return_value = self._rows()
        self.client.get(self.url, HTTP_REQUEST_TOKEN=TEST_TOKEN)
        record = PageAnalytics.objects.get(page_path=self.community_path)
        self.assertEqual(record.community, self.community)
        self.assertEqual(record.object_id, self.community.pk)
        self.assertEqual(record.content_type, PageAnalytics.ContentType.COMMUNITY)

    @patch('analytics.views.fetch_page_report')
    def test_invalid_token_returns_401(self, mock_fetch):
        res = self.client.get(self.url, HTTP_REQUEST_TOKEN='wrong-token')
        self.assertEqual(res.status_code, 401)
        mock_fetch.assert_not_called()

    @patch('analytics.views.fetch_page_report')
    def test_missing_token_returns_401(self, mock_fetch):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 401)
        mock_fetch.assert_not_called()

    @patch('analytics.views.fetch_page_report')
    def test_post_method_returns_405(self, mock_fetch):
        res = self.client.post(self.url, HTTP_REQUEST_TOKEN=TEST_TOKEN)
        self.assertEqual(res.status_code, 405)
        mock_fetch.assert_not_called()

    @patch('analytics.views.fetch_page_report')
    def test_date_param_is_passed_to_fetch(self, mock_fetch):
        mock_fetch.return_value = []
        self.client.get(
            self.url, {'date': '2026-05-20'}, HTTP_REQUEST_TOKEN=TEST_TOKEN
        )
        mock_fetch.assert_called_once()
        _, called_date = mock_fetch.call_args[0]
        self.assertEqual(called_date, date(2026, 5, 20))

    @patch('analytics.views.fetch_page_report')
    def test_invalid_date_param_returns_400(self, mock_fetch):
        res = self.client.get(
            self.url, {'date': 'not-a-date'}, HTTP_REQUEST_TOKEN=TEST_TOKEN
        )
        self.assertEqual(res.status_code, 400)
        mock_fetch.assert_not_called()

    @patch('analytics.views.fetch_page_report')
    def test_default_date_is_previous_localdate(self, mock_fetch):
        # date 未指定時は TIME_ZONE 基準の前日（UTC ではなく localdate）を取りに行く
        mock_fetch.return_value = []
        self.client.get(self.url, HTTP_REQUEST_TOKEN=TEST_TOKEN)
        mock_fetch.assert_called_once()
        _, called_date = mock_fetch.call_args[0]
        self.assertEqual(called_date, timezone.localdate() - timedelta(days=1))

    @override_settings(REQUEST_TOKEN='')
    @patch('analytics.views.fetch_page_report')
    def test_empty_configured_token_rejects_all(self, mock_fetch):
        # REQUEST_TOKEN が空設定の場合、ヘッダ未送信でも fail-closed で 401
        res_no_header = self.client.get(self.url)
        self.assertEqual(res_no_header.status_code, 401)
        res_empty_header = self.client.get(self.url, HTTP_REQUEST_TOKEN='')
        self.assertEqual(res_empty_header.status_code, 401)
        mock_fetch.assert_not_called()
