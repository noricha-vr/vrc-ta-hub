"""マイページ（SettingsView）のアクセス解析セクションの表示・権限テスト.

ログインオーナーには自分の community の集計のみが出て、他人の community の
名前・数値が一切出ないこと、未ログインは login へリダイレクトされることを検証する。
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

    def test_owner_sees_only_own_analytics(self):
        """ログインオーナーには自分の集会の集計のみが出て、他人の数値・流入元が出ない."""
        self.client.force_login(self.owner)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('daily_series', response.context)
        self.assertIn('source_breakdown', response.context)

        # 自分の流入元は出る
        self.assertContains(response, 'mine-source-only / organic')
        # 他人の流入元・固有数値は出ない（権限境界の検証）
        self.assertNotContains(response, 'other-source-only / referral')

        # 集計は自分の community(pv=77) のみ。他人の pv=999 が混入しない
        sources = {row['source_medium'] for row in response.context['source_breakdown']}
        self.assertIn('mine-source-only / organic', sources)
        self.assertNotIn('other-source-only / referral', sources)

    def test_chart_section_rendered_for_owner(self):
        """集計データがあるオーナーには canvas と Chart.js が出る."""
        self.client.force_login(self.owner)
        response = self.client.get(self.url)
        self.assertContains(response, 'id="analytics-daily-chart"')
        self.assertContains(response, 'cdn.jsdelivr.net/npm/chart.js')

    def test_user_without_community_has_empty_series(self):
        """所属 community が無いユーザーは集計が空（グラフ canvas は出ない）."""
        no_comm_user = create_discord_linked_user(
            user_name='no_comm', email='nocomm@example.com', password='pass',
        )
        self.client.force_login(no_comm_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        # context キーは存在するが空（accessible_community_ids が空のため）
        self.assertEqual(response.context['daily_series'], [])
        self.assertEqual(response.context['source_breakdown'], [])
        self.assertNotContains(response, 'id="analytics-daily-chart"')
