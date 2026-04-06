from unittest.mock import patch

from django.test import RequestFactory, TestCase

from news.models import Category, Post


class PostModelTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.category = Category.objects.create(name="お知らせ", slug="news", order=1)

    def test_get_meta_description_uses_explicit_value_and_truncates(self):
        post = Post.objects.create(
            title="明示説明あり",
            slug="explicit-description",
            body_markdown="本文",
            meta_description="a" * 200,
            category=self.category,
        )

        self.assertEqual(post.get_meta_description(), "a" * 160)

    def test_get_meta_description_generates_from_markdown(self):
        post = Post.objects.create(
            title="本文から生成",
            slug="generated-description",
            body_markdown="# 見出し\n**本文** と [リンク](https://example.com) を含む",
            category=self.category,
        )

        self.assertEqual(
            post.get_meta_description(),
            "見出し 本文 と リンクhttps://example.com を含む",
        )

    def test_get_absolute_thumbnail_url_builds_absolute_url_from_request(self):
        post = Post.objects.create(
            title="相対URL",
            slug="relative-thumbnail",
            body_markdown="本文",
            category=self.category,
        )
        post.thumbnail.name = "news/thumbnail.jpg"
        request = self.factory.get("/")

        self.assertEqual(
            post.get_absolute_thumbnail_url(request),
            "http://testserver/media/news/thumbnail.jpg",
        )

    def test_get_absolute_thumbnail_url_uses_default_domain_without_request(self):
        post = Post.objects.create(
            title="requestなし",
            slug="thumbnail-without-request",
            body_markdown="本文",
            category=self.category,
        )
        post.thumbnail.name = "news/thumbnail.jpg"

        self.assertEqual(
            post.get_absolute_thumbnail_url(),
            "https://vrc-ta-hub.com/media/news/thumbnail.jpg",
        )

    def test_get_absolute_thumbnail_url_returns_absolute_storage_url_as_is(self):
        post = Post.objects.create(
            title="絶対URL",
            slug="absolute-thumbnail",
            body_markdown="本文",
            category=self.category,
        )
        post.thumbnail.name = "news/thumbnail.jpg"

        with patch.object(
            post.thumbnail.storage,
            "url",
            return_value="https://cdn.example.com/news/thumbnail.jpg",
        ):
            self.assertEqual(
                post.get_absolute_thumbnail_url(),
                "https://cdn.example.com/news/thumbnail.jpg",
            )

    def test_get_absolute_thumbnail_url_returns_default_image_when_thumbnail_missing(self):
        post = Post.objects.create(
            title="サムネイルなし",
            slug="no-thumbnail",
            body_markdown="本文",
            category=self.category,
        )

        self.assertEqual(
            post.get_absolute_thumbnail_url(),
            "https://data.vrc-ta-hub.com/images/twitter-negipan-1600.jpeg",
        )
