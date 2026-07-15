"""sitemap アプリのテスト"""
import xml.etree.ElementTree as ET
from datetime import date, time
from urllib.parse import urlparse

from django.test import Client, TestCase
from django.urls import reverse

from community.models import Community
from event.models import Event, EventDetail

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


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

    def test_robots_txt_sitemap_line_is_clean(self):
        """robots.txt の Sitemap 行が二重スラッシュを含まない正しい URL であること"""
        response = self.client.get("/robots.txt")
        body = response.content.decode("utf-8")
        self.assertIn("Sitemap: https://vrc-ta-hub.com/sitemap.xml", body)
        # 過去に含まれていた誤った二重スラッシュが再発しないことを検証
        self.assertNotIn("//sitemap.xml", body)


class SitemapUrlsResolveTestCase(TestCase):
    """sitemap.xml の全 <loc> が実在する URL であり、実在しない URL が混入していないこと"""

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
        cls.pending_community = Community.objects.create(
            name="未承認集会",
            status="pending",
            frequency="不定期",
        )

    def setUp(self):
        self.client = Client()

    def _get_sitemap_response(self):
        return self.client.get(reverse("sitemap:sitemap"))

    def _extract_locs(self, response):
        """レスポンス XML から名前空間付きで <loc> 一覧を抽出する"""
        root = ET.fromstring(response.content)
        return [loc.text for loc in root.findall("sm:url/sm:loc", SITEMAP_NS)]

    def test_all_loc_urls_return_200(self):
        """sitemap.xml の全 <loc> が 200 で応答すること（実在しない URL の再発防止）"""
        response = self._get_sitemap_response()
        locs = self._extract_locs(response)
        self.assertGreater(len(locs), 0, "sitemap に <loc> が 1 件も存在しない")

        for loc in locs:
            # loc は絶対 URL 想定。path 部分だけ取り出して同一クライアントで叩く
            path = urlparse(loc).path
            with self.subTest(loc=loc):
                sub_response = self.client.get(path)
                self.assertEqual(
                    sub_response.status_code,
                    200,
                    f"sitemap の URL が 200 を返さない: {loc} (path={path})",
                )

    def test_sitemap_has_no_priority_tag(self):
        """<priority> タグが完全に除去されていること（Google は無視するので不要）"""
        response = self._get_sitemap_response()
        self.assertNotIn(b"<priority>", response.content)

    def test_sitemap_excludes_removed_paths(self):
        """sitemap から削除したパスが混入していないこと"""
        response = self._get_sitemap_response()
        body = response.content.decode("utf-8")
        # 過去に本番 404 を出した実在しないパス
        self.assertNotIn("event/detail/list", body)
        # 認証ページは sitemap に載せない方針
        self.assertNotIn("account/login", body)
        self.assertNotIn("account/register", body)
