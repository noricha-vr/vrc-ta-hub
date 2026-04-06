"""news.views のテスト"""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
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
