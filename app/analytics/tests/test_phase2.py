"""Phase 2 #10 #11 のテスト（未紐付けトラフィック / ポスタークリック）。"""
from datetime import date, timedelta
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from analytics import services
from analytics.models import PageAnalytics, PosterClick
from community.models import Community, CommunityMember
from user_account.models import CustomUser

TEST_TOKEN = 'test-token-phase2'


def _create_community(name='C'):
    return Community.objects.create(
        name=name, description='', organizers='x', platform='PCVR',
        status='approved', frequency='毎週', start_time='22:00',
    )


@override_settings(REQUEST_TOKEN=TEST_TOKEN, GA4_PROPERTY_ID='123456789')
class GlobalTrafficSyncTest(TestCase):
    """resolve できない URL は GLOBAL レコードとして保存されることを検証。"""

    def setUp(self):
        self.url = reverse('analytics:sync')

    @patch('analytics.views.fetch_poster_click_report', return_value=[])
    @patch('analytics.views.fetch_page_report')
    def test_unmapped_url_saved_as_global(self, mock_fetch, _mock_poster):
        mock_fetch.return_value = [
            {
                'page_path': '/about/',
                'date': '2026-05-31',
                'source_medium': 'google / organic',
                'pv': 50, 'users': 40, 'sessions': 45,
            },
        ]
        res = self.client.get(self.url, HTTP_REQUEST_TOKEN=TEST_TOKEN)
        self.assertEqual(res.status_code, 200)
        about = PageAnalytics.objects.get(page_path='/about/')
        self.assertEqual(about.content_type, PageAnalytics.ContentType.GLOBAL)
        self.assertIsNone(about.community)
        self.assertEqual(about.object_id, 0)


class GlobalTrafficServiceTest(TestCase):
    """get_global_traffic が GLOBAL レコードのみ集計することを検証。"""

    def setUp(self):
        community = _create_community('A')
        today = timezone.localdate()
        # community 紐付き（GLOBAL ではない）
        PageAnalytics.objects.create(
            page_path=f'/community/{community.pk}/', date=today,
            content_type=PageAnalytics.ContentType.COMMUNITY,
            community=community, object_id=community.pk,
            pv=100, users=80, sessions=90, source_medium='google / organic',
        )
        # GLOBAL レコード
        PageAnalytics.objects.create(
            page_path='/', date=today,
            content_type=PageAnalytics.ContentType.GLOBAL,
            community=None, object_id=0,
            pv=500, users=400, sessions=450, source_medium='google / organic',
        )
        PageAnalytics.objects.create(
            page_path='/community/list/', date=today,
            content_type=PageAnalytics.ContentType.GLOBAL,
            community=None, object_id=0,
            pv=200, users=180, sessions=190, source_medium='(direct) / (none)',
        )

    def test_only_global_records_counted(self):
        result = services.get_global_traffic(days=30)
        # 500 + 200 = 700（community 紐付きは含めない）
        self.assertEqual(result['total']['pv'], 700)
        # top_paths も GLOBAL のみ
        paths = [r['page_path'] for r in result['top_paths']]
        self.assertIn('/', paths)
        self.assertIn('/community/list/', paths)
        for p in paths:
            self.assertFalse(p.startswith('/community/1/'), 'community URL must not appear')


@override_settings(REQUEST_TOKEN=TEST_TOKEN, GA4_PROPERTY_ID='123456789')
class PosterClickSyncTest(TestCase):
    """fetch_poster_click_report 結果が PosterClick に保存されることを検証。"""

    def setUp(self):
        self.community = _create_community('Poster A')
        self.url = reverse('analytics:sync')

    @patch('analytics.views.fetch_poster_click_report')
    @patch('analytics.views.fetch_page_report', return_value=[])
    def test_poster_click_saved(self, _mock_fetch, mock_poster):
        mock_poster.return_value = [
            {'community_id': self.community.pk, 'clicks': 42, 'users': 30},
        ]
        res = self.client.get(self.url, HTTP_REQUEST_TOKEN=TEST_TOKEN)
        self.assertEqual(res.status_code, 200)
        pc = PosterClick.objects.get(community=self.community)
        self.assertEqual(pc.clicks, 42)
        self.assertEqual(pc.users, 30)

    @patch('analytics.views.fetch_poster_click_report')
    @patch('analytics.views.fetch_page_report', return_value=[])
    def test_poster_click_unknown_community_skipped(self, _mock_fetch, mock_poster):
        """削除済み community の poster_click は無視（外部キー違反を防ぐ）。"""
        mock_poster.return_value = [
            {'community_id': 999999, 'clicks': 10, 'users': 5},
        ]
        res = self.client.get(self.url, HTTP_REQUEST_TOKEN=TEST_TOKEN)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(PosterClick.objects.count(), 0)

    @patch('analytics.views.fetch_poster_click_report', side_effect=Exception('GA4 error'))
    @patch('analytics.views.fetch_page_report', return_value=[])
    def test_poster_click_fetch_failure_does_not_break_sync(self, _mock_fetch, _mock_poster):
        """poster_click 取得が失敗しても全体は 200 を返す（page_view sync を守る）。"""
        res = self.client.get(self.url, HTTP_REQUEST_TOKEN=TEST_TOKEN)
        self.assertEqual(res.status_code, 200)


class PosterClickServiceTest(TestCase):
    """get_poster_click_stats の権限境界を検証。"""

    def setUp(self):
        self.community_a = _create_community('PA')
        self.community_b = _create_community('PB')
        today = timezone.localdate()
        PosterClick.objects.create(community=self.community_a, date=today, clicks=20, users=15)
        PosterClick.objects.create(community=self.community_b, date=today, clicks=99, users=80)

    def test_filter_by_community_ids(self):
        """渡された community_ids のレコードのみ集計に含まれる。"""
        result = services.get_poster_click_stats([self.community_a.pk], days=30)
        self.assertEqual(result['total']['clicks'], 20)
        per = result['per_community']
        self.assertEqual(len(per), 1)
        self.assertEqual(per[0]['community_id'], self.community_a.pk)

    def test_empty_community_ids_returns_zero(self):
        result = services.get_poster_click_stats([], days=30)
        self.assertEqual(result['total']['clicks'], 0)
        self.assertEqual(result['per_community'], [])


class DashboardGlobalTrafficVisibilityTest(TestCase):
    """サイト全体トラフィック section は superuser だけ context に入る。"""

    def setUp(self):
        self.client = Client()
        self.community = _create_community('M')
        self.user = CustomUser.objects.create_user(
            user_name='owner_m', email='m@example.com', password='pass',
        )
        CommunityMember.objects.create(
            community=self.community, user=self.user,
            role=CommunityMember.Role.OWNER,
        )
        today = timezone.localdate()
        PageAnalytics.objects.create(
            page_path='/', date=today,
            content_type=PageAnalytics.ContentType.GLOBAL,
            community=None, object_id=0,
            pv=300, users=200, sessions=250, source_medium='google / organic',
        )

    def test_owner_does_not_see_global_traffic(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('analytics:dashboard'))
        self.assertNotIn('global_traffic', response.context)

    def test_superuser_sees_global_traffic(self):
        admin = CustomUser.objects.create_superuser(
            user_name='admin_g', email='ag@example.com', password='pass',
        )
        self.client.force_login(admin)
        response = self.client.get(reverse('analytics:dashboard'))
        self.assertIn('global_traffic', response.context)
        self.assertEqual(response.context['global_traffic']['total']['pv'], 300)
