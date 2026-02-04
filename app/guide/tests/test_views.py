"""ガイドビューのテスト"""
from django.test import TestCase, Client
from django.urls import reverse

from guide.views import (
    load_navigation,
    get_flat_nav_items,
    find_adjacent_pages,
    get_breadcrumbs,
    load_markdown_content,
    NavItem,
)


class NavItemTest(TestCase):
    """NavItemクラスのテスト"""

    def test_nav_item_url(self):
        """pathを持つNavItemがURLを返すこと"""
        item = NavItem(title="テスト", path="test/page")
        self.assertEqual(item.url, "/guide/test/page/")

    def test_nav_item_url_none(self):
        """pathがNoneの場合はURLもNoneを返すこと"""
        item = NavItem(title="テスト", path=None)
        self.assertIsNone(item.url)

    def test_nav_item_has_children(self):
        """子要素を持つかどうかを正しく判定すること"""
        parent = NavItem(
            title="親",
            children=[NavItem(title="子", path="child")]
        )
        child = NavItem(title="子", path="child")

        self.assertTrue(parent.has_children)
        self.assertFalse(child.has_children)

    def test_nav_item_owner_only(self):
        """owner_onlyフラグを正しく保持すること"""
        owner_item = NavItem(title="主催者のみ", owner_only=True)
        normal_item = NavItem(title="通常")

        self.assertTrue(owner_item.owner_only)
        self.assertFalse(normal_item.owner_only)


class LoadNavigationTest(TestCase):
    """ナビゲーション読み込みのテスト"""

    def test_load_navigation(self):
        """ナビゲーションが読み込めること"""
        nav_items = load_navigation()

        # ナビゲーションが存在すること
        self.assertIsInstance(nav_items, list)
        self.assertGreater(len(nav_items), 0)

    def test_load_navigation_structure(self):
        """ナビゲーション構造が正しいこと"""
        nav_items = load_navigation()

        # 最初のアイテムが存在
        first_item = nav_items[0]
        self.assertIsInstance(first_item, NavItem)
        self.assertTrue(first_item.title)


class GetFlatNavItemsTest(TestCase):
    """フラット化ナビゲーションのテスト"""

    def test_get_flat_nav_items(self):
        """ナビゲーションがフラット化されること"""
        nav_items = [
            NavItem(title="はじめに", path="index"),
            NavItem(
                title="親カテゴリ",
                children=[
                    NavItem(title="子1", path="child1"),
                    NavItem(title="子2", path="child2"),
                ]
            ),
        ]

        flat_items = get_flat_nav_items(nav_items)

        # pathを持つアイテムのみが含まれる
        self.assertEqual(len(flat_items), 3)
        paths = [item.path for item in flat_items]
        self.assertEqual(paths, ["index", "child1", "child2"])


class FindAdjacentPagesTest(TestCase):
    """前後ページ取得のテスト"""

    def setUp(self):
        self.nav_items = [
            NavItem(title="1", path="page1"),
            NavItem(title="2", path="page2"),
            NavItem(title="3", path="page3"),
        ]

    def test_find_adjacent_first_page(self):
        """最初のページの場合、前のページがNoneであること"""
        prev_page, next_page = find_adjacent_pages(self.nav_items, "page1")

        self.assertIsNone(prev_page)
        self.assertEqual(next_page.path, "page2")

    def test_find_adjacent_middle_page(self):
        """中間のページの場合、前後のページが取得できること"""
        prev_page, next_page = find_adjacent_pages(self.nav_items, "page2")

        self.assertEqual(prev_page.path, "page1")
        self.assertEqual(next_page.path, "page3")

    def test_find_adjacent_last_page(self):
        """最後のページの場合、次のページがNoneであること"""
        prev_page, next_page = find_adjacent_pages(self.nav_items, "page3")

        self.assertEqual(prev_page.path, "page2")
        self.assertIsNone(next_page)

    def test_find_adjacent_not_found(self):
        """存在しないパスの場合、両方Noneであること"""
        prev_page, next_page = find_adjacent_pages(self.nav_items, "notexist")

        self.assertIsNone(prev_page)
        self.assertIsNone(next_page)


class GetBreadcrumbsTest(TestCase):
    """パンくずリスト生成のテスト"""

    def test_get_breadcrumbs(self):
        """パンくずリストが生成されること"""
        nav_items = [
            NavItem(
                title="集会を管理する",
                children=[
                    NavItem(title="集会を登録する", path="community/create"),
                ]
            ),
        ]

        breadcrumbs = get_breadcrumbs(nav_items, "community/create")

        # ホーム、ガイド、カテゴリ、ページの4つ
        self.assertEqual(len(breadcrumbs), 4)
        self.assertEqual(breadcrumbs[0]["title"], "ホーム")
        self.assertEqual(breadcrumbs[1]["title"], "使い方ガイド")
        self.assertEqual(breadcrumbs[2]["title"], "集会を管理する")
        self.assertEqual(breadcrumbs[3]["title"], "集会を登録する")


class LoadMarkdownContentTest(TestCase):
    """マークダウン読み込みのテスト"""

    def test_load_markdown_content(self):
        """マークダウンファイルが読み込めること"""
        content, frontmatter = load_markdown_content("index")

        # HTMLが返されること
        self.assertIn("<p>", content)

        # フロントマターが解析されること
        self.assertEqual(frontmatter.get("title"), "はじめに")

    def test_load_markdown_content_nested(self):
        """ネストしたパスのマークダウンが読み込めること"""
        content, frontmatter = load_markdown_content("community/create")

        self.assertIn("<p>", content)
        self.assertEqual(frontmatter.get("title"), "集会を登録する")

    def test_load_markdown_content_not_found(self):
        """存在しないファイルの場合は404エラーが発生すること"""
        from django.http import Http404

        with self.assertRaises(Http404):
            load_markdown_content("nonexistent/page")

    def test_load_markdown_content_path_traversal_double_dot(self):
        """パストラバーサル攻撃（../）がブロックされること"""
        from django.http import Http404

        with self.assertRaises(Http404):
            load_markdown_content("../../../etc/passwd")

    def test_load_markdown_content_path_traversal_nested(self):
        """ネストしたパストラバーサル攻撃がブロックされること"""
        from django.http import Http404

        with self.assertRaises(Http404):
            load_markdown_content("community/../../../etc/passwd")

    def test_load_markdown_content_path_traversal_encoded(self):
        """パストラバーサル攻撃（%2e%2e）がブロックされること"""
        from django.http import Http404

        # URL decodeされた状態で渡される可能性がある
        with self.assertRaises(Http404):
            load_markdown_content("..%2f..%2f..%2fetc%2fpasswd")

    def test_load_markdown_content_path_traversal_absolute(self):
        """絶対パスによるアクセスがブロックされること"""
        from django.http import Http404

        with self.assertRaises(Http404):
            load_markdown_content("/etc/passwd")


class GuideViewsTest(TestCase):
    """ガイドビューの統合テスト"""

    def setUp(self):
        self.client = Client()

    def test_guide_index_view(self):
        """インデックスページが表示されること"""
        response = self.client.get(reverse("guide:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "使い方ガイド")
        self.assertIn("nav_items", response.context)

    def test_guide_page_view(self):
        """個別ページが表示されること"""
        response = self.client.get(
            reverse("guide:page", kwargs={"path": "index"})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "はじめに")

    def test_guide_page_view_nested(self):
        """ネストしたパスのページが表示されること"""
        response = self.client.get(
            reverse("guide:page", kwargs={"path": "community/create"})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "集会を登録する")

    def test_guide_page_view_not_found(self):
        """存在しないページは404を返すこと"""
        response = self.client.get(
            reverse("guide:page", kwargs={"path": "nonexistent"})
        )

        self.assertEqual(response.status_code, 404)

    def test_guide_page_has_navigation(self):
        """ページにナビゲーションが含まれること"""
        response = self.client.get(
            reverse("guide:page", kwargs={"path": "index"})
        )

        self.assertIn("nav_items", response.context)
        self.assertIn("prev_page", response.context)
        self.assertIn("next_page", response.context)

    def test_guide_page_has_breadcrumbs(self):
        """ページにパンくずリストが含まれること"""
        response = self.client.get(
            reverse("guide:page", kwargs={"path": "community/create"})
        )

        self.assertIn("breadcrumbs", response.context)
        breadcrumbs = response.context["breadcrumbs"]
        self.assertGreater(len(breadcrumbs), 2)

    def test_guide_page_view_path_traversal_double_dot(self):
        """パストラバーサル攻撃（../）が404を返すこと"""
        response = self.client.get(
            reverse("guide:page", kwargs={"path": "../../../etc/passwd"})
        )

        self.assertEqual(response.status_code, 404)

    def test_guide_page_view_path_traversal_nested(self):
        """ネストしたパストラバーサル攻撃が404を返すこと"""
        response = self.client.get(
            reverse("guide:page", kwargs={"path": "community/../../../etc/passwd"})
        )

        self.assertEqual(response.status_code, 404)

    def test_guide_promotion_poster_page(self):
        """promotion/posterページが表示されること"""
        response = self.client.get(
            reverse("guide:page", kwargs={"path": "promotion/poster"})
        )

        self.assertEqual(response.status_code, 200)
        # タイトル（フロントマターのtitle）
        self.assertContains(response, "ポスターを掲示する")
        # 本文の内容
        self.assertContains(response, "ワールドへのポスター掲示")
