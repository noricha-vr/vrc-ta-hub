import json
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from news.models import Category, Post
from user_account.models import CustomUser


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class NewsViewTest(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.staff_user = CustomUser.objects.create_user(
            user_name="staff-user",
            email="staff@example.com",
            password="password",
            is_staff=True,
        )
        self.category = Category.objects.get(slug="activity")
        self.other_category = Category.objects.create(name="更新", slug="updates", order=2)
        published_at = timezone.now() - timedelta(days=1)

        self.published_post = Post.objects.create(
            title="公開記事",
            slug="published-post",
            body_markdown="公開本文",
            category=self.category,
            is_published=True,
            published_at=published_at,
        )
        self.unpublished_post = Post.objects.create(
            title="非公開記事",
            slug="draft-post",
            body_markdown="非公開本文",
            category=self.category,
            is_published=False,
        )
        self.other_category_post = Post.objects.create(
            title="別カテゴリ記事",
            slug="other-category-post",
            body_markdown="別カテゴリ本文",
            category=self.other_category,
            is_published=True,
            published_at=published_at - timedelta(hours=1),
        )

    def test_post_list_view_hides_unpublished_posts_from_anonymous_user(self):
        response = self.client.get(reverse("news:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "公開記事")
        self.assertNotContains(response, "非公開記事")
        category_slugs = [category.slug for category in response.context["categories"]]
        self.assertIn(self.category.slug, category_slugs)
        self.assertIn(self.other_category.slug, category_slugs)
        self.assertIsNotNone(cache.get("news_categories"))

        structured_data = json.loads(response.context["structured_data_json"])
        self.assertEqual(structured_data[1]["@type"], "CollectionPage")
        item_names = [
            item["name"] for item in structured_data[1]["mainEntity"]["itemListElement"]
        ]
        self.assertIn("公開記事", item_names)
        self.assertIn("別カテゴリ記事", item_names)
        self.assertNotIn("非公開記事", item_names)

    def test_post_list_view_shows_unpublished_posts_to_staff_user(self):
        self.client.force_login(self.staff_user)

        response = self.client.get(reverse("news:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "公開記事")
        self.assertContains(response, "非公開記事")

    def test_post_detail_view_returns_404_for_unpublished_post_to_anonymous_user(self):
        response = self.client.get(
            reverse("news:detail", kwargs={"slug": self.unpublished_post.slug})
        )

        self.assertEqual(response.status_code, 404)

    def test_post_detail_view_shows_unpublished_post_to_staff_user(self):
        self.client.force_login(self.staff_user)

        response = self.client.get(
            reverse("news:detail", kwargs={"slug": self.unpublished_post.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "非公開記事")
        self.assertContains(response, "非公開")
        structured_data = json.loads(response.context["structured_data_json"])
        self.assertEqual(structured_data["@type"], "BlogPosting")
        self.assertEqual(structured_data["headline"], "非公開記事")

    def test_category_list_view_filters_by_category_and_publication(self):
        response = self.client.get(
            reverse("news:category_list", kwargs={"category_slug": self.category.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "公開記事")
        self.assertNotContains(response, "非公開記事")
        self.assertNotContains(response, "別カテゴリ記事")
        self.assertEqual(response.context["category"], self.category)

    def test_record_redirect_points_to_activity_category(self):
        response = self.client.get(reverse("news:record_redirect"))

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], "/news/category/activity/")
