"""news.models のテスト"""
from django.test import TestCase

from news.models import Category, Post


class CategoryModelTestCase(TestCase):
    """Category モデルの基本テスト"""

    def test_str_returns_name(self):
        """__str__ がカテゴリ名を返す"""
        category = Category(name="お知らせ", slug="announcement", order=0)
        self.assertEqual(str(category), "お知らせ")

    def test_ordering_by_order_then_name(self):
        """order -> name の順でソートされる"""
        # マイグレーションで初期カテゴリが存在するため、テスト用スラッグでフィルタ
        Category.objects.create(name="B更新情報", slug="test-updates", order=20)
        Category.objects.create(name="Aお知らせ", slug="test-announcement", order=10)
        Category.objects.create(name="Cリリース", slug="test-release", order=10)

        test_slugs = {"test-updates", "test-announcement", "test-release"}
        categories = list(
            Category.objects.filter(slug__in=test_slugs).values_list("name", flat=True)
        )
        self.assertEqual(categories, ["Aお知らせ", "Cリリース", "B更新情報"])

    def test_slug_unique_constraint(self):
        """slug のユニーク制約が機能する"""
        Category.objects.create(name="お知らせ", slug="announcement", order=0)
        with self.assertRaises(Exception):
            Category.objects.create(name="別カテゴリ", slug="announcement", order=1)


class PostModelTestCase(TestCase):
    """Post モデルの基本テスト"""

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(
            name="お知らせ", slug="announcement", order=0
        )

    def _make_post(self, **kwargs):
        defaults = {
            "title": "テスト記事",
            "slug": "test-post",
            "body_markdown": "# テスト\n\nこれはテスト記事です。",
            "category": self.category,
            "is_published": False,
        }
        defaults.update(kwargs)
        return Post(**defaults)

    def test_str_returns_title(self):
        """__str__ が記事タイトルを返す"""
        post = self._make_post(title="テスト記事タイトル")
        self.assertEqual(str(post), "テスト記事タイトル")


class PostGetMetaDescriptionTestCase(TestCase):
    """Post.get_meta_description のテスト"""

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(
            name="お知らせ", slug="announcement", order=0
        )

    def _make_post(self, **kwargs):
        defaults = {
            "title": "テスト記事",
            "slug": "test-meta",
            "body_markdown": "",
            "category": self.category,
        }
        defaults.update(kwargs)
        return Post(**defaults)

    def test_returns_meta_description_if_set(self):
        """meta_description が設定されていればそのまま返す"""
        post = self._make_post(meta_description="カスタム説明文")
        self.assertEqual(post.get_meta_description(), "カスタム説明文")

    def test_meta_description_truncated_to_max_length(self):
        """meta_description が max_length で切り詰められる"""
        long_desc = "あ" * 200
        post = self._make_post(meta_description=long_desc)
        result = post.get_meta_description(max_length=10)
        self.assertEqual(len(result), 10)
        self.assertEqual(result, "あ" * 10)

    def test_fallback_to_body_markdown(self):
        """meta_description が空なら body_markdown から生成"""
        post = self._make_post(
            body_markdown="## 見出し\n\nこれは本文です。",
            meta_description="",
        )
        result = post.get_meta_description()
        self.assertNotIn("#", result)
        self.assertIn("見出し", result)
        self.assertIn("これは本文です", result)

    def test_fallback_strips_markdown_syntax(self):
        """Markdown記法（#, *, _, ` 等）が除去される"""
        post = self._make_post(
            body_markdown="# **太字** _斜体_ `コード` [リンク](url)",
            meta_description="",
        )
        result = post.get_meta_description()
        self.assertNotIn("#", result)
        self.assertNotIn("*", result)
        self.assertNotIn("_", result)
        self.assertNotIn("`", result)
        self.assertNotIn("[", result)
        self.assertNotIn("]", result)

    def test_fallback_body_truncated_to_max_length(self):
        """body_markdown からの生成も max_length で切り詰められる"""
        post = self._make_post(
            body_markdown="テスト文章 " * 100,
            meta_description="",
        )
        result = post.get_meta_description(max_length=20)
        self.assertLessEqual(len(result), 20)

    def test_empty_body_returns_empty_string(self):
        """body_markdown が空なら空文字列を返す"""
        post = self._make_post(body_markdown="", meta_description="")
        result = post.get_meta_description()
        self.assertEqual(result, "")


class PostGetAbsoluteThumbnailUrlTestCase(TestCase):
    """Post.get_absolute_thumbnail_url のテスト"""

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(
            name="お知らせ", slug="announcement", order=0
        )

    def test_no_thumbnail_returns_default(self):
        """サムネイル未設定ならデフォルト画像 URL を返す"""
        post = Post(
            title="テスト",
            slug="test-no-thumb",
            body_markdown="",
            category=self.category,
        )
        result = post.get_absolute_thumbnail_url()
        self.assertEqual(
            result,
            "https://data.vrc-ta-hub.com/images/twitter-negipan-1600.jpeg",
        )
