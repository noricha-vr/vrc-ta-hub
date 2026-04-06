"""news.forms のテスト"""
from django.test import TestCase
from django.utils import timezone

from news.forms import PostForm
from news.models import Category


class PostFormSaveTestCase(TestCase):
    """PostForm.save の公開日時自動設定テスト"""

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(
            name="お知らせ", slug="announcement", order=0
        )

    def _valid_data(self, **overrides):
        """フォーム送信用の基本データ"""
        data = {
            "title": "テスト記事",
            "slug": "test-form-post",
            "body_markdown": "本文テスト",
            "meta_description": "",
            "category": self.category.pk,
            "is_published": False,
        }
        data.update(overrides)
        return data

    def test_publish_sets_published_at(self):
        """is_published=True で published_at が未設定なら現在日時が設定される"""
        data = self._valid_data(is_published=True)
        form = PostForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)
        post = form.save()
        self.assertIsNotNone(post.published_at)

    def test_unpublish_clears_published_at(self):
        """is_published=False にすると published_at がクリアされる"""
        data = self._valid_data(is_published=True, slug="test-unpublish")
        form = PostForm(data=data)
        post = form.save()
        self.assertIsNotNone(post.published_at)

        # 非公開にする
        update_data = self._valid_data(is_published=False, slug="test-unpublish")
        form2 = PostForm(data=update_data, instance=post)
        self.assertTrue(form2.is_valid(), form2.errors)
        post2 = form2.save()
        self.assertIsNone(post2.published_at)

    def test_already_published_keeps_published_at(self):
        """既に published_at が設定済みなら上書きしない"""
        fixed_time = timezone.now()
        data = self._valid_data(is_published=True, slug="test-keep-date")
        form = PostForm(data=data)
        post = form.save()
        post.published_at = fixed_time
        post.save()

        # 再度保存しても published_at は変わらない
        update_data = self._valid_data(is_published=True, slug="test-keep-date")
        form2 = PostForm(data=update_data, instance=post)
        self.assertTrue(form2.is_valid(), form2.errors)
        post2 = form2.save()
        self.assertEqual(post2.published_at, fixed_time)
