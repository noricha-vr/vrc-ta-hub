"""sitemap アプリのテスト"""
from datetime import date, time

from django.test import Client, TestCase
from django.urls import reverse

from community.models import Community
from event.models import Event, EventDetail


class SitemapViewTestCase(TestCase):
    """SitemapView の基本テスト"""

    @classmethod
    def setUpTestData(cls):
        cls.community = Community.objects.create(
            name="テスト集会",
            status="approved",
            frequency="毎週",
        )
        cls.event = Event.objects.create(
            community=cls.community,
            date=date(2026, 4, 6),
            start_time=time(22, 0),
        )
        cls.event_detail = EventDetail.objects.create(
            event=cls.event,
            theme="テスト発表",
            speaker="テスト発表者",
            status="approved",
            detail_type="LT",
        )
        # 非承認のデータ（サイトマップに含まれないことを検証用）
        cls.pending_community = Community.objects.create(
            name="未承認集会",
            status="pending",
            frequency="不定期",
        )

    def setUp(self):
        self.client = Client()

    def test_sitemap_returns_200(self):
        """サイトマップが 200 を返す"""
        response = self.client.get(reverse("sitemap:sitemap"))
        self.assertEqual(response.status_code, 200)

    def test_sitemap_content_type_is_xml(self):
        """Content-Type が application/xml"""
        response = self.client.get(reverse("sitemap:sitemap"))
        self.assertEqual(response["Content-Type"], "application/xml")

    def test_sitemap_contains_approved_community(self):
        """承認済みコミュニティがサイトマップに含まれる"""
        response = self.client.get(reverse("sitemap:sitemap"))
        # コンテキストで確認
        communities = response.context["communities"]
        community_names = [c.name for c in communities]
        self.assertIn("テスト集会", community_names)

    def test_sitemap_excludes_pending_community(self):
        """未承認コミュニティがサイトマップに含まれない"""
        response = self.client.get(reverse("sitemap:sitemap"))
        communities = response.context["communities"]
        community_names = [c.name for c in communities]
        self.assertNotIn("未承認集会", community_names)

    def test_sitemap_contains_approved_event_details(self):
        """承認済みイベント詳細がサイトマップに含まれる"""
        response = self.client.get(reverse("sitemap:sitemap"))
        event_details = response.context["event_details"]
        themes = [ed.theme for ed in event_details]
        self.assertIn("テスト発表", themes)

    def test_sitemap_context_has_base_url(self):
        """コンテキストに base_url が含まれる"""
        response = self.client.get(reverse("sitemap:sitemap"))
        self.assertIn("base_url", response.context)
        self.assertTrue(response.context["base_url"].startswith("https://"))


class SitemapRedirectTestCase(TestCase):
    """sitemaps.xml -> sitemap.xml のリダイレクトテスト"""

    def setUp(self):
        self.client = Client()

    def test_sitemaps_xml_redirects_to_sitemap_xml(self):
        """sitemaps.xml が sitemap.xml にリダイレクトされる"""
        response = self.client.get("/sitemaps.xml")
        self.assertEqual(response.status_code, 301)
        self.assertIn("/sitemap.xml", response["Location"])


class RobotsTxtTestCase(TestCase):
    """robots.txt のテスト"""

    def setUp(self):
        self.client = Client()

    def test_robots_txt_returns_200(self):
        """robots.txt が 200 を返す"""
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
