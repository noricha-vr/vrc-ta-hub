"""発表記事の外部発信（#683）: AI 要約の明記と、詳細ページの構造化データ（JSON-LD）。"""

import json
from datetime import date

from django.core.files.base import ContentFile
from django.test import TestCase
from django.urls import reverse

from event.models import EventDetail
from tests.factories import make_community, make_event, make_event_detail

AI_NOTICE = 'この記事は発表動画・スライドをもとに AI が要約したものです'


class ArticleAiNoticeAndStructuredDataTest(TestCase):
    def setUp(self):
        self.community = make_community(name='記事テスト集会')
        self.event = make_event(self.community, event_date=date(2026, 9, 12))
        self.detail = make_event_detail(
            self.event, status='approved', speaker='山田 太郎', theme='記事のテーマ',
            h1='記事のタイトル', contents='## 見出し\n\n本文です。', meta_description='記事の要約です。',
            article_consent=EventDetail.ArticleConsent.OK,
            youtube_url='https://www.youtube.com/watch?v=rrKl0s23E0M',
        )
        self.url = reverse('event:detail', kwargs={'pk': self.detail.pk})

    def _make_auto(self):
        """自動生成のまま（タイトル・本文・要約のハッシュが保存済みの値と一致）にする。"""
        EventDetail.objects.filter(pk=self.detail.pk).update(
            article_body_hash=self.detail.current_article_hash(),
        )

    def _structured(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        return json.loads(response.context['structured_data_json'])

    def test_auto_generated_article_shows_ai_notice(self):
        """自動生成のままの記事には AI 要約の注記が出る。"""
        self._make_auto()
        self.assertContains(self.client.get(self.url), AI_NOTICE)

    def test_manually_edited_article_has_no_ai_notice(self):
        """手動で編集した記事（ハッシュと一致しない）には出ない。"""
        self._make_auto()
        EventDetail.objects.filter(pk=self.detail.pk).update(contents='## 見出し\n\n手で直した本文です。')
        self.assertNotContains(self.client.get(self.url), AI_NOTICE)

    def test_article_without_hash_is_manual_and_has_no_ai_notice(self):
        """ハッシュの無い記事（手書き・旧記事）は手動扱いで出ない。"""
        self.assertNotContains(self.client.get(self.url), AI_NOTICE)

    def test_ng_article_has_no_ai_notice(self):
        """記事化 NG の発表には記事ごと出さない。"""
        self._make_auto()
        EventDetail.objects.filter(pk=self.detail.pk).update(article_consent=EventDetail.ArticleConsent.NG)
        self.assertNotContains(self.client.get(self.url), AI_NOTICE)

    def test_structured_data_author_date_and_thumbnail(self):
        """JSON-LD の author は発表者名、datePublished は開催日、image はサムネイルが先頭。"""
        self.detail.thumbnail_image.save('thumb.jpg', ContentFile(b'jpeg'), save=True)
        data = self._structured()

        self.assertEqual(data['@type'], 'BlogPosting')
        self.assertEqual(data['author'], {'@type': 'Person', 'name': '山田 太郎'})
        self.assertEqual(data['datePublished'], '2026-09-12')
        self.assertIn('thumb', data['image'][0])
        self.assertEqual(data['image'][0], f'http://testserver{self.detail.thumbnail_image.url}'
                         if self.detail.thumbnail_image.url.startswith('/') else self.detail.thumbnail_image.url)

    def test_structured_data_for_lt_without_article_is_absent(self):
        """記事の無い LT には出さない。"""
        EventDetail.objects.filter(pk=self.detail.pk).update(h1='', contents='')
        response = self.client.get(self.url)
        self.assertNotIn('structured_data_json', response.context)
