"""キャンペーン集計サービスのテスト。"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from community.models import Community, CommunityMember

from analytics import services
from analytics.models import Campaign, PageAnalytics

User = get_user_model()


class CampaignBreakdownTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user_a = User.objects.create_user(
            user_name='userA', email='a@example.com', password='pass-a',
        )
        cls.community_a = Community.objects.create(
            name='集会A', frequency='毎週', organizers='主催A',
        )
        cls.community_b = Community.objects.create(
            name='集会B', frequency='毎週', organizers='主催B',
        )
        CommunityMember.objects.create(
            community=cls.community_a, user=cls.user_a,
            role=CommunityMember.Role.OWNER,
        )
        today = timezone.localdate()

        # 集会A: flyer キャンペーン × 2日分
        for offset in (0, 1):
            PageAnalytics.objects.create(
                page_path=f'/community/{cls.community_a.pk}/',
                date=today - timedelta(days=offset),
                content_type=PageAnalytics.ContentType.COMMUNITY,
                community=cls.community_a, object_id=cls.community_a.pk,
                pv=5, users=4, sessions=5,
                source_medium='(direct) / (none)',
                campaign='20260510-gishohaku',
            )
        # 集会A: (not set) 流入
        PageAnalytics.objects.create(
            page_path=f'/community/{cls.community_a.pk}/', date=today,
            content_type=PageAnalytics.ContentType.COMMUNITY,
            community=cls.community_a, object_id=cls.community_a.pk,
            pv=100, users=90, sessions=95,
            source_medium='google / organic',
            campaign='(not set)',
        )
        # 集会B: 別キャンペーン（user_a は見えてはいけない）
        PageAnalytics.objects.create(
            page_path=f'/community/{cls.community_b.pk}/', date=today,
            content_type=PageAnalytics.ContentType.COMMUNITY,
            community=cls.community_b, object_id=cls.community_b.pk,
            pv=999, users=999, sessions=999,
            source_medium='flyer / qr',
            campaign='other-campaign',
        )

        cls.campaign = Campaign.objects.create(
            community=cls.community_a, name='技術書博 5/10 配布',
            utm_source='flyer', utm_medium='qr', utm_campaign='20260510-gishohaku',
        )

    def test_breakdown_excludes_other_community(self):
        ids = services.accessible_community_ids(self.user_a)
        rows = services.get_campaign_breakdown(ids, days=30)
        utm_keys = {r['campaign'] for r in rows}
        self.assertIn('20260510-gishohaku', utm_keys)
        self.assertNotIn('other-campaign', utm_keys)  # 他集会のキャンペーンは混入しない

    def test_breakdown_excludes_not_set_by_default(self):
        ids = services.accessible_community_ids(self.user_a)
        rows = services.get_campaign_breakdown(ids, days=30)
        keys = {r['campaign'] for r in rows}
        self.assertNotIn('(not set)', keys)

    def test_breakdown_can_include_not_set(self):
        ids = services.accessible_community_ids(self.user_a)
        rows = services.get_campaign_breakdown(ids, days=30, exclude_default=False)
        keys = {r['campaign'] for r in rows}
        self.assertIn('(not set)', keys)

    def test_breakdown_aggregates_by_campaign(self):
        ids = services.accessible_community_ids(self.user_a)
        rows = services.get_campaign_breakdown(ids, days=30)
        target = next(r for r in rows if r['campaign'] == '20260510-gishohaku')
        # 2日分 × 5pv = 10pv
        self.assertEqual(target['pv'], 10)

    def test_breakdown_attaches_campaign_meta(self):
        ids = services.accessible_community_ids(self.user_a)
        rows = services.get_campaign_breakdown(ids, days=30)
        target = next(r for r in rows if r['campaign'] == '20260510-gishohaku')
        self.assertIsNotNone(target['meta'])
        self.assertEqual(target['meta']['name'], '技術書博 5/10 配布')
        self.assertEqual(target['meta']['community_name'], '集会A')

    def test_daily_series_returns_chart_format(self):
        ids = services.accessible_community_ids(self.user_a)
        result = services.get_campaign_daily_series(ids, days=7)
        self.assertIn('labels', result)
        self.assertIn('datasets', result)
        if result['datasets']:
            self.assertEqual(len(result['datasets'][0]['data']), len(result['labels']))

    def test_empty_community_ids_returns_empty(self):
        self.assertEqual(services.get_campaign_breakdown([], days=30), [])
        self.assertEqual(
            services.get_campaign_daily_series([], days=30),
            {'labels': [], 'datasets': []},
        )


class CampaignBreakdownAcrossCommunitiesTest(TestCase):
    """同一 utm_campaign を別 community が使った場合に行が分離されることを確認する。

    Campaign.Meta.unique_together = ('community', 'utm_campaign') なので別 community 間で
    同じ utm_campaign を使うことは仕様上許される（運用上の意図的な分離）。superuser や
    複数集会を持つユーザーから見た時に集計が混ざらないことを担保する。
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            user_name='multiOwner', email='multi@example.com', password='pass',
        )
        cls.community_a = Community.objects.create(
            name='集会A', frequency='毎週', organizers='主催A',
        )
        cls.community_b = Community.objects.create(
            name='集会B', frequency='毎週', organizers='主催B',
        )
        # ユーザーは両方の owner
        CommunityMember.objects.create(
            community=cls.community_a, user=cls.user,
            role=CommunityMember.Role.OWNER,
        )
        CommunityMember.objects.create(
            community=cls.community_b, user=cls.user,
            role=CommunityMember.Role.OWNER,
        )
        today = timezone.localdate()
        # 同じ utm_campaign を別 community が使うケース
        PageAnalytics.objects.create(
            page_path=f'/community/{cls.community_a.pk}/', date=today,
            content_type=PageAnalytics.ContentType.COMMUNITY,
            community=cls.community_a, object_id=cls.community_a.pk,
            pv=10, users=8, sessions=9,
            source_medium='(direct) / (none)', campaign='shared-key',
        )
        PageAnalytics.objects.create(
            page_path=f'/community/{cls.community_b.pk}/', date=today,
            content_type=PageAnalytics.ContentType.COMMUNITY,
            community=cls.community_b, object_id=cls.community_b.pk,
            pv=20, users=18, sessions=19,
            source_medium='(direct) / (none)', campaign='shared-key',
        )
        # Campaign メタも両方
        cls.cm_a = Campaign.objects.create(
            community=cls.community_a, name='集会Aの shared',
            utm_source='flyer', utm_medium='qr', utm_campaign='shared-key',
        )
        cls.cm_b = Campaign.objects.create(
            community=cls.community_b, name='集会Bの shared',
            utm_source='flyer', utm_medium='qr', utm_campaign='shared-key',
        )

    def test_same_utm_campaign_returns_separate_rows_per_community(self):
        ids = services.accessible_community_ids(self.user)
        rows = services.get_campaign_breakdown(ids, days=30)
        # 同じ utm_campaign で2行（community_a / community_b）
        self.assertEqual(len(rows), 2)
        by_community = {r['community_id']: r for r in rows}
        self.assertEqual(by_community[self.community_a.pk]['pv'], 10)
        self.assertEqual(by_community[self.community_b.pk]['pv'], 20)
        # meta も community に対応した正しい Campaign に紐付く
        self.assertEqual(by_community[self.community_a.pk]['meta']['name'], '集会Aの shared')
        self.assertEqual(by_community[self.community_b.pk]['meta']['name'], '集会Bの shared')


class CampaignBreakdownIncludesRootLandingTest(TestCase):
    """landing_path='/' で Campaign 経由解決された PageAnalytics が
    主催者ダッシュボードに表示されることの回帰テスト。
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            user_name='owner', email='owner@example.com', password='pass',
        )
        cls.community = Community.objects.create(
            name='集会X', frequency='毎週', organizers='主催X',
        )
        CommunityMember.objects.create(
            community=cls.community, user=cls.user,
            role=CommunityMember.Role.OWNER,
        )
        cls.campaign = Campaign.objects.create(
            community=cls.community, name='トップ着地チラシ',
            utm_source='flyer', utm_medium='qr',
            utm_campaign='20260510-flyer-top', landing_path='/',
        )
        # sync_analytics の解決後に保存される想定レコード（page_path='/' で CAMPAIGN）
        PageAnalytics.objects.create(
            page_path='/', date=timezone.localdate(),
            content_type=PageAnalytics.ContentType.CAMPAIGN,
            community=cls.community, object_id=cls.campaign.pk,
            pv=42, users=38, sessions=40,
            source_medium='flyer / qr', campaign='20260510-flyer-top',
        )

    def test_root_landing_appears_in_owner_dashboard(self):
        ids = services.accessible_community_ids(self.user)
        rows = services.get_campaign_breakdown(ids, days=30)
        target = next(
            (r for r in rows if r['campaign'] == '20260510-flyer-top'), None,
        )
        self.assertIsNotNone(target, '改修前はこのレコードが GLOBAL になり消えていた')
        self.assertEqual(target['pv'], 42)
        self.assertEqual(target['community_id'], self.community.pk)
        self.assertEqual(target['meta']['name'], 'トップ着地チラシ')
