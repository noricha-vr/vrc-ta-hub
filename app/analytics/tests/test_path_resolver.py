from datetime import date

from django.test import TestCase

from community.models import Community
from event.models import Event, EventDetail

from analytics.models import Campaign, PageAnalytics
from analytics.path_resolver import resolve_page_path


class ResolvePagePathTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.community = Community.objects.create(
            name='テスト集会',
            frequency='毎週',
            organizers='主催A',
        )
        cls.event = Event.objects.create(
            community=cls.community,
            date=date(2026, 5, 1),
            weekday='Thu',
        )
        cls.event_detail = EventDetail.objects.create(
            event=cls.event,
            detail_type='LT',
        )

    def test_community_path_resolves(self):
        result = resolve_page_path(f'/community/{self.community.pk}/')
        self.assertIsNotNone(result)
        self.assertEqual(result['content_type'], PageAnalytics.ContentType.COMMUNITY)
        self.assertEqual(result['community'], self.community)
        self.assertEqual(result['object_id'], self.community.pk)

    def test_event_detail_path_resolves_community_via_event(self):
        result = resolve_page_path(f'/event/detail/{self.event_detail.pk}/')
        self.assertIsNotNone(result)
        self.assertEqual(result['content_type'], PageAnalytics.ContentType.EVENT_DETAIL)
        # EventDetail に直接 community FK は無いので event 経由で解決される
        self.assertEqual(result['community'], self.community)
        self.assertEqual(result['object_id'], self.event_detail.pk)

    def test_community_path_with_query_string_is_none(self):
        self.assertIsNone(resolve_page_path(f'/community/{self.community.pk}/?x=1'))

    def test_community_path_without_trailing_slash_is_none(self):
        self.assertIsNone(resolve_page_path(f'/community/{self.community.pk}'))

    def test_non_numeric_pk_is_none(self):
        self.assertIsNone(resolve_page_path('/community/abc/'))

    def test_unrelated_path_is_none(self):
        self.assertIsNone(resolve_page_path('/about/'))

    def test_nonexistent_community_pk_is_none(self):
        self.assertIsNone(resolve_page_path('/community/999999/'))

    def test_nonexistent_event_detail_pk_is_none(self):
        self.assertIsNone(resolve_page_path('/event/detail/999999/'))

    def test_empty_path_is_none(self):
        self.assertIsNone(resolve_page_path(''))


class ResolveByCampaignTest(TestCase):
    """pagePath で解決できないときに utm_campaign 経由で community を引けることを確認する。

    landing_path='/' のチラシ QR が GLOBAL になって主催者のキャンペーン集計から
    消えてしまう問題（PR #383 codex 指摘 r3338246662）の回帰防止。
    """

    @classmethod
    def setUpTestData(cls):
        cls.community = Community.objects.create(
            name='集会X', frequency='毎週', organizers='主催X',
        )
        cls.other_community = Community.objects.create(
            name='集会Y', frequency='毎週', organizers='主催Y',
        )
        cls.campaign = Campaign.objects.create(
            community=cls.community, name='トップ着地チラシ',
            utm_source='flyer', utm_medium='qr',
            utm_campaign='20260510-gishohaku', landing_path='/',
        )

    def test_root_path_with_campaign_resolves_via_campaign(self):
        result = resolve_page_path('/', '20260510-gishohaku')
        self.assertIsNotNone(result)
        self.assertEqual(result['content_type'], PageAnalytics.ContentType.CAMPAIGN)
        self.assertEqual(result['community'], self.community)
        self.assertEqual(result['object_id'], self.campaign.pk)

    def test_unrelated_path_with_campaign_resolves_via_campaign(self):
        # トップ以外のサイト全体ページ（/about/ 等）でも、Campaign が紐付けば取り込む
        result = resolve_page_path('/about/', '20260510-gishohaku')
        self.assertIsNotNone(result)
        self.assertEqual(result['content_type'], PageAnalytics.ContentType.CAMPAIGN)
        self.assertEqual(result['community'], self.community)

    def test_not_set_campaign_falls_back_to_none(self):
        # GA4 が utm_campaign 未指定セッションに付ける標準ラベル `(not set)` は無視
        self.assertIsNone(resolve_page_path('/', '(not set)'))

    def test_unknown_campaign_falls_back_to_none(self):
        # Campaign テーブルに存在しない utm_campaign は紐付けず GLOBAL に倒す
        self.assertIsNone(resolve_page_path('/', 'unknown-campaign'))

    def test_path_resolves_first_when_both_match(self):
        """pagePath が community 直接マッチする場合は Campaign 解決より優先される。"""
        result = resolve_page_path(
            f'/community/{self.community.pk}/', '20260510-gishohaku'
        )
        # pagePath が直接 community を指していれば content_type は COMMUNITY のまま
        self.assertEqual(result['content_type'], PageAnalytics.ContentType.COMMUNITY)
        self.assertEqual(result['object_id'], self.community.pk)

    def test_ambiguous_campaign_across_communities_is_not_resolved(self):
        """同一 utm_campaign が複数 community に存在する場合は誤割り当てを避けて None。"""
        Campaign.objects.create(
            community=self.other_community, name='同名キャンペーン',
            utm_source='flyer', utm_medium='qr',
            utm_campaign='shared-key', landing_path='/',
        )
        Campaign.objects.create(
            community=self.community, name='同名キャンペーン（別集会）',
            utm_source='flyer', utm_medium='qr',
            utm_campaign='shared-key', landing_path='/',
        )
        # 2件マッチするのでどの community に紐付けるか曖昧 → None で GLOBAL 扱い
        self.assertIsNone(resolve_page_path('/', 'shared-key'))
