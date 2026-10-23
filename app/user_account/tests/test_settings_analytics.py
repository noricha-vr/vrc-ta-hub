"""マイページ（SettingsView）のアクセス解析の導線テスト.

設定画面には解析の数字・グラフを出さず、見られる集会がある時だけダッシュボードへの導線を出す。
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from analytics.models import PageAnalytics
from community.models import Community, CommunityMember
from user_account.tests.utils import create_discord_linked_user

User = get_user_model()


class SettingsAnalyticsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Discord 連携済みにしてミドルウェアのリダイレクトを回避する
        cls.owner = create_discord_linked_user(
            user_name='my_owner', email='owner@example.com', password='pass',
        )
        cls.other_owner = create_discord_linked_user(
            user_name='my_other', email='other@example.com', password='pass',
        )

        cls.my_community = Community.objects.create(
            name='自分の集会', status='approved', frequency='毎週', organizers='主催',
        )
        cls.other_community = Community.objects.create(
            name='他人の集会XYZ', status='approved', frequency='毎週', organizers='別主催',
        )
        CommunityMember.objects.create(
            community=cls.my_community, user=cls.owner,
            role=CommunityMember.Role.OWNER,
        )
        CommunityMember.objects.create(
            community=cls.other_community, user=cls.other_owner,
            role=CommunityMember.Role.OWNER,
        )

        # 集計対象は前日まで（当日は GA4 未同期で集計外）。前日にデータを置く
        yesterday = timezone.localdate() - timedelta(days=1)
        PageAnalytics.objects.create(
            page_path=f'/community/{cls.my_community.pk}/', date=yesterday,
            content_type=PageAnalytics.ContentType.COMMUNITY,
            community=cls.my_community, object_id=cls.my_community.pk,
            pv=77, users=60, sessions=70, source_medium='mine-source-only / organic',
        )
        PageAnalytics.objects.create(
            page_path=f'/community/{cls.other_community.pk}/', date=yesterday,
            content_type=PageAnalytics.ContentType.COMMUNITY,
            community=cls.other_community, object_id=cls.other_community.pk,
            pv=999, users=900, sessions=950, source_medium='other-source-only / referral',
        )

    def setUp(self):
        self.client = Client()
        self.url = reverse('account:settings')

    def test_requires_login(self):
        """未ログインは login へリダイレクトされる."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)

    def test_owner_sees_link_but_no_numbers(self):
        """オーナーには導線だけが出て、集計値・流入元・グラフは出ない."""
        self.client.force_login(self.owner)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['has_analytics_access'])
        self.assertContains(response, f'href="{reverse("analytics:dashboard")}"')
        self.assertContains(response, 'アクセス解析を見る')
        for key in ('daily_series', 'source_breakdown'):
            self.assertNotIn(key, response.context)
        self.assertNotContains(response, 'mine-source-only / organic')
        self.assertNotContains(response, 'other-source-only / referral')
        self.assertNotContains(response, 'id="analytics-daily-chart"')
        self.assertNotContains(response, 'cdn.jsdelivr.net/npm/chart.js')

    def test_superuser_without_membership_sees_link(self):
        """superuser は所属が無くても全集会を見られるので導線が出る."""
        admin = create_discord_linked_user(
            user_name='admin_user', email='admin@example.com', password='pass', is_superuser=True,
        )
        self.client.force_login(admin)
        response = self.client.get(self.url)
        self.assertTrue(response.context['has_analytics_access'])
        self.assertContains(response, 'アクセス解析を見る')

    def test_user_without_community_has_no_link(self):
        """所属 community が無いユーザーには導線が出ない."""
        no_comm_user = create_discord_linked_user(
            user_name='no_comm', email='nocomm@example.com', password='pass',
        )
        self.client.force_login(no_comm_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['has_analytics_access'])
        self.assertNotContains(response, 'アクセス解析を見る')
        self.assertNotContains(response, f'href="{reverse("analytics:dashboard")}"')
