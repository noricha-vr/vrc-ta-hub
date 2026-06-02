"""Campaign モデルのバリデーション・unique・url プロパティのテスト。"""
from django.db import IntegrityError
from django.test import TestCase, override_settings

from community.models import Community

from analytics.models import Campaign


@override_settings(SITE_URL='https://vrc-ta-hub.example')
class CampaignModelTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.community = Community.objects.create(
            name='集会A', frequency='毎週', organizers='主催A',
        )

    def test_url_builds_with_utm_params(self):
        c = Campaign.objects.create(
            community=self.community, name='5/10 配布',
            utm_source='flyer', utm_medium='qr', utm_campaign='20260510-gishohaku',
            landing_path='/',
        )
        # url は SITE_URL + landing_path + UTM クエリの組み立て。
        # クエリのパラメータ順は urlencode の挿入順（source→medium→campaign）。
        # SITE_URL は website.constants で末尾 / が除去された値を使う想定。
        self.assertIn('utm_source=flyer', c.url)
        self.assertIn('utm_medium=qr', c.url)
        self.assertIn('utm_campaign=20260510-gishohaku', c.url)
        self.assertTrue(c.url.startswith('https://'))

    def test_url_with_landing_path(self):
        c = Campaign.objects.create(
            community=self.community, name='集会LP',
            utm_source='poster', utm_medium='qr', utm_campaign='community-poster',
            landing_path=f'/community/{self.community.pk}/',
        )
        self.assertIn(f'/community/{self.community.pk}/?', c.url)

    def test_unique_per_community_and_utm_campaign(self):
        """同一 community 内で utm_campaign が重複できない。"""
        Campaign.objects.create(
            community=self.community, name='A',
            utm_source='flyer', utm_medium='qr', utm_campaign='dup-key',
        )
        with self.assertRaises(IntegrityError):
            Campaign.objects.create(
                community=self.community, name='B',
                utm_source='poster', utm_medium='qr', utm_campaign='dup-key',
            )

    def test_same_utm_campaign_allowed_across_communities(self):
        """別 community なら同じ utm_campaign を使える（運用上の意図的な分離）。"""
        other = Community.objects.create(
            name='集会B', frequency='毎週', organizers='主催B',
        )
        Campaign.objects.create(
            community=self.community, name='A',
            utm_source='flyer', utm_medium='qr', utm_campaign='shared-key',
        )
        # 例外が出ないこと
        Campaign.objects.create(
            community=other, name='B',
            utm_source='flyer', utm_medium='qr', utm_campaign='shared-key',
        )
