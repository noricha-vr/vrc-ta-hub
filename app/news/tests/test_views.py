"""news.views のテスト"""
import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from news.models import Category, Post

CustomUser = get_user_model()


class PostListViewTestCase(TestCase):
    """PostListView の基本レスポンステスト"""

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(
            name="お知らせ", slug="announcement", order=0
        )
        cls.published_post = Post.objects.create(
            title="公開記事",
            slug="published-post",
            body_markdown="公開された記事の本文です。",
            category=cls.category,
            is_published=True,
            published_at=timezone.now(),
        )
        cls.draft_post = Post.objects.create(
            title="下書き記事",
            slug="draft-post",
            body_markdown="下書きの本文です。",
            category=cls.category,
            is_published=False,
        )

    def setUp(self):
        self.client = Client()

    def test_list_returns_200(self):
        """一覧ページが 200 を返す"""
        response = self.client.get(reverse("news:list"))
        self.assertEqual(response.status_code, 200)

    def test_anonymous_sees_only_published(self):
        """未ログインユーザーには公開記事のみ表示"""
        response = self.client.get(reverse("news:list"))
        self.assertContains(response, "公開記事")
        self.assertNotContains(response, "下書き記事")

    def test_staff_sees_all_posts(self):
        """スタッフユーザーには下書きも表示"""
        staff = CustomUser.objects.create_user(
            email="staff@example.com",
            password="testpass123",
            user_name="スタッフ",
            is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.get(reverse("news:list"))
        self.assertContains(response, "公開記事")
        self.assertContains(response, "下書き記事")

    def test_context_contains_categories(self):
        """コンテキストにカテゴリ一覧が含まれる"""
        response = self.client.get(reverse("news:list"))
        self.assertIn("categories", response.context)

    def test_context_contains_structured_data(self):
        """コンテキストに構造化データ JSON が含まれる"""
        response = self.client.get(reverse("news:list"))
        self.assertIn("structured_data_json", response.context)


class PostDetailViewTestCase(TestCase):
    """PostDetailView の基本レスポンステスト"""

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(
            name="お知らせ", slug="announcement", order=0
        )
        cls.published_post = Post.objects.create(
            title="公開記事詳細",
            slug="detail-published",
            body_markdown="詳細ページの本文です。",
            category=cls.category,
            is_published=True,
            published_at=timezone.now(),
        )
        cls.draft_post = Post.objects.create(
            title="下書き詳細",
            slug="detail-draft",
            body_markdown="下書き詳細の本文。",
            category=cls.category,
            is_published=False,
        )

    def setUp(self):
        self.client = Client()

    def test_published_detail_returns_200(self):
        """公開記事の詳細ページが 200 を返す"""
        url = reverse("news:detail", kwargs={"slug": self.published_post.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_anonymous_cannot_see_draft(self):
        """未ログインユーザーは下書き記事にアクセスできない"""
        url = reverse("news:detail", kwargs={"slug": self.draft_post.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_staff_can_see_draft(self):
        """スタッフユーザーは下書き記事にアクセスできる"""
        staff = CustomUser.objects.create_user(
            email="staff-detail@example.com",
            password="testpass123",
            user_name="スタッフ詳細",
            is_staff=True,
        )
        self.client.force_login(staff)
        url = reverse("news:detail", kwargs={"slug": self.draft_post.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_detail_contains_structured_data(self):
        """詳細ページのコンテキストに構造化データ JSON が含まれる"""
        url = reverse("news:detail", kwargs={"slug": self.published_post.slug})
        response = self.client.get(url)
        self.assertIn("structured_data_json", response.context)

    @override_settings(STATIC_URL="/static/")
    def test_detail_displays_mapped_thumbnail(self):
        """専用画像URLを本文と全メタデータに反映する"""
        post = self._create_archive_post()
        expected_url = (
            "https://vrc-ta-hub.com/static/news/images/og/"
            "vket-2026-summer-video-archive-v1.png"
        )
        response = self.client.get(
            reverse("news:detail", kwargs={"slug": post.slug}),
            secure=True,
            HTTP_HOST="vrc-ta-hub.com",
        )
        structured_data = json.loads(response.context["structured_data_json"])

        self.assertContains(response, f'src="{expected_url}"')
        self.assertContains(
            response,
            f'<meta property="og:image" content="{expected_url}">',
        )
        self.assertContains(
            response,
            f'<meta name="twitter:image" content="{expected_url}">',
        )
        self.assertEqual(structured_data["image"], [expected_url])

    @override_settings(STATIC_URL="/static/")
    def test_detail_mapped_thumbnail_has_accessible_og_metadata(self):
        """専用画像の代替テキストと実寸をOGPへ反映する"""
        post = self._create_archive_post()
        response = self.client.get(
            reverse("news:detail", kwargs={"slug": post.slug})
        )

        self.assertContains(
            response,
            f'<meta property="og:image:alt" content="{post.title}">',
        )
        self.assertContains(
            response,
            f'<meta name="twitter:image:alt" content="{post.title}">',
        )
        self.assertContains(
            response,
            '<meta property="og:image:width" content="1200">',
        )
        self.assertContains(
            response,
            '<meta property="og:image:height" content="630">',
        )

    def test_detail_mapped_thumbnail_uses_original_ratio_and_mobile_gutter(self):
        """専用画像を元比率とBootstrap gutterで表示する"""
        post = self._create_archive_post()
        response = self.client.get(
            reverse("news:detail", kwargs={"slug": post.slug})
        )

        self.assertContains(
            response,
            '<div class="detail-thumbnail-container detail-thumbnail-container--static">',
        )
        self.assertContains(response, "padding-bottom: 52.5%;")
        self.assertContains(response, "object-fit: contain;")
        self.assertContains(
            response,
            "margin-inline: calc(var(--bs-gutter-x) * -0.5);",
        )

    def test_detail_uploaded_thumbnail_overrides_mapped_thumbnail(self):
        """アップロード画像を本文と全メタデータで専用画像より優先する"""
        post = self._create_archive_post(thumbnail="news/uploaded.png")
        expected_url = "https://vrc-ta-hub.com/media/news/uploaded.png"
        response = self.client.get(
            reverse("news:detail", kwargs={"slug": post.slug}),
            secure=True,
            HTTP_HOST="vrc-ta-hub.com",
        )
        structured_data = json.loads(response.context["structured_data_json"])

        self.assertContains(response, f'src="{expected_url}"')
        self.assertContains(
            response,
            f'<meta property="og:image" content="{expected_url}">',
        )
        self.assertContains(
            response,
            f'<meta name="twitter:image" content="{expected_url}">',
        )
        self.assertContains(
            response,
            f'<meta property="og:image:alt" content="{post.title}">',
        )
        self.assertContains(
            response,
            f'<meta name="twitter:image:alt" content="{post.title}">',
        )
        self.assertNotContains(response, 'property="og:image:width"')
        self.assertNotContains(response, 'property="og:image:height"')
        self.assertContains(
            response,
            '<div class="detail-thumbnail-container">',
        )
        self.assertEqual(structured_data["image"], [expected_url])

    def _create_archive_post(self, thumbnail: str = "") -> Post:
        return Post.objects.create(
            title="Vket 2026 Summer 動画アーカイブ",
            slug="vket-2026-summer",
            body_markdown="動画アーカイブの本文です。",
            category=self.category,
            thumbnail=thumbnail,
            is_published=True,
            published_at=timezone.now(),
        )


class CategoryListViewTestCase(TestCase):
    """CategoryListView の基本レスポンステスト"""

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(
            name="更新情報", slug="updates", order=0
        )
        cls.post = Post.objects.create(
            title="更新情報の記事",
            slug="updates-post",
            body_markdown="更新情報カテゴリの記事です。",
            category=cls.category,
            is_published=True,
            published_at=timezone.now(),
        )

    def setUp(self):
        self.client = Client()

    def test_category_list_returns_200(self):
        """カテゴリ別一覧が 200 を返す"""
        url = reverse(
            "news:category_list",
            kwargs={"category_slug": self.category.slug},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_category_list_shows_only_matching_posts(self):
        """カテゴリに属する記事のみ表示される"""
        other_cat = Category.objects.create(
            name="リリース", slug="release", order=1
        )
        Post.objects.create(
            title="リリースの記事",
            slug="release-post",
            body_markdown="リリースカテゴリの記事。",
            category=other_cat,
            is_published=True,
            published_at=timezone.now(),
        )
        url = reverse(
            "news:category_list",
            kwargs={"category_slug": self.category.slug},
        )
        response = self.client.get(url)
        self.assertContains(response, "更新情報の記事")
        self.assertNotContains(response, "リリースの記事")

    def test_nonexistent_category_returns_404(self):
        """存在しないカテゴリスラッグで 404 を返す"""
        url = reverse(
            "news:category_list",
            kwargs={"category_slug": "nonexistent"},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_context_contains_category(self):
        """コンテキストに対象カテゴリが含まれる"""
        url = reverse(
            "news:category_list",
            kwargs={"category_slug": self.category.slug},
        )
        response = self.client.get(url)
        self.assertEqual(response.context["category"], self.category)


class StaffOnlyViewsTestCase(TestCase):
    """スタッフ限定ビュー（Create/Update/Delete）のアクセス制御テスト"""

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(
            name="お知らせ", slug="announcement", order=0
        )
        cls.post = Post.objects.create(
            title="編集対象記事",
            slug="editable-post",
            body_markdown="編集テスト用。",
            category=cls.category,
            is_published=True,
            published_at=timezone.now(),
        )

    def setUp(self):
        self.client = Client()

    def test_anonymous_redirected_from_create(self):
        """未ログインユーザーは記事作成ページからリダイレクトされる"""
        response = self.client.get(reverse("news:create"))
        self.assertEqual(response.status_code, 302)

    def test_anonymous_redirected_from_edit(self):
        """未ログインユーザーは記事編集ページからリダイレクトされる"""
        url = reverse("news:edit", kwargs={"slug": self.post.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_anonymous_redirected_from_delete(self):
        """未ログインユーザーは記事削除ページからリダイレクトされる"""
        url = reverse("news:delete", kwargs={"slug": self.post.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_non_staff_cannot_create(self):
        """非スタッフユーザーは記事作成ページにアクセスできない"""
        user = CustomUser.objects.create_user(
            email="normal@example.com",
            password="testpass123",
            user_name="一般ユーザー",
            is_staff=False,
        )
        self.client.force_login(user)
        response = self.client.get(reverse("news:create"))
        self.assertEqual(response.status_code, 403)

    def test_staff_can_access_create(self):
        """スタッフユーザーは記事作成ページにアクセスできる"""
        staff = CustomUser.objects.create_user(
            email="staff-create@example.com",
            password="testpass123",
            user_name="スタッフ作成",
            is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.get(reverse("news:create"))
        self.assertEqual(response.status_code, 200)

    def test_staff_can_access_edit(self):
        """スタッフユーザーは記事編集ページにアクセスできる"""
        staff = CustomUser.objects.create_user(
            email="staff-edit@example.com",
            password="testpass123",
            user_name="スタッフ編集",
            is_staff=True,
        )
        self.client.force_login(staff)
        url = reverse("news:edit", kwargs={"slug": self.post.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
