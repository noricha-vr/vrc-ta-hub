"""API キーで発表に字幕を登録し、記事生成の字幕キャッシュに入ることのテスト。"""

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.messages import get_messages
from django.db.models.signals import post_save
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from event.models import EventDetail
from event.services.article_generation import process_article_generation_queue
from event.services.content_generation_service import ARTICLE_EDITED_MESSAGE, fetch_transcript
from tests.factories import make_community, make_event, make_event_detail, make_user
from user_account.models import APIKey


class EventDetailTranscriptAPITest(TestCase):
    def setUp(self):
        self.client = APIClient()
        owner = make_user(user_name='transcript_owner', email='transcript_owner@example.com')
        self.owner = owner
        _key, raw_key = APIKey.create_with_raw_key(user=owner, name='字幕テスト')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {raw_key}')
        self.event = make_event(make_community(name='字幕の集会', owner=owner))
        self.detail = make_event_detail(self.event, status='approved', youtube_url='https://youtu.be/QDYMKH1hbbA')

    def _patch(self, body):
        return self.client.patch(f'/api/v1/event-details/{self.detail.pk}/', body, format='json')

    def test_transcript_fills_the_cache_used_by_article_generation(self):
        response = self._patch({'transcript': 'はい、始めます'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn('transcript', response.data)
        detail = EventDetail.objects.get(pk=self.detail.pk)
        self.assertEqual((detail.cached_transcript, detail.cached_transcript_video_id), ('はい、始めます', 'QDYMKH1hbbA'))
        self.assertEqual(fetch_transcript(detail), 'はい、始めます')

    def test_transcript_only_patch_does_not_save_the_detail(self):
        saved = []

        def on_save(sender, instance, **kwargs):
            saved.append(instance.pk)

        post_save.connect(on_save, sender=EventDetail)
        try:
            response = self._patch({'transcript': '字幕だけ'})
        finally:
            post_save.disconnect(on_save, sender=EventDetail)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(saved, [])
        self.assertEqual(EventDetail.objects.get(pk=self.detail.pk).cached_transcript, '字幕だけ')

    def test_queues_article_generation_for_consented_talk(self):
        EventDetail.objects.filter(pk=self.detail.pk).update(
            article_consent=EventDetail.ArticleConsent.OK, article_generation_requested_at=None)

        self._patch({'transcript': '字幕'})

        self.assertIsNotNone(EventDetail.objects.get(pk=self.detail.pk).article_generation_requested_at)

    def test_new_transcript_for_the_same_video_is_not_skipped_as_unchanged(self):
        from event.services.article_generation import _skip_reason
        EventDetail.objects.filter(pk=self.detail.pk).update(article_consent=EventDetail.ArticleConsent.OK)
        detail = EventDetail.objects.get(pk=self.detail.pk)
        detail.h1, detail.contents = '自動の記事', '本文'
        detail.save(update_fields=['h1', 'contents'])
        detail.record_generated_body(detail.article_sources())
        detail.save(update_fields=['article_source_video_id', 'article_source_slide_name', 'article_body_hash',
                                   'article_generated_at'])
        self.assertEqual(_skip_reason(EventDetail.objects.get(pk=self.detail.pk)), 'unchanged')

        self._patch({'transcript': '訂正した字幕'})

        self.assertEqual(_skip_reason(EventDetail.objects.get(pk=self.detail.pk)), '')

    def test_does_not_queue_without_article_consent_or_over_a_manual_article(self):
        self._patch({'transcript': '字幕'})
        self.assertIsNone(EventDetail.objects.get(pk=self.detail.pk).article_generation_requested_at)

        EventDetail.objects.filter(pk=self.detail.pk).update(
            article_consent=EventDetail.ArticleConsent.OK, h1='手書きの記事', contents='本文',
            article_body_hash='', article_generation_requested_at=None)
        self._patch({'transcript': '字幕2'})
        self.assertIsNone(EventDetail.objects.get(pk=self.detail.pk).article_generation_requested_at)

    def test_transcript_follows_a_new_video_url_in_the_same_request(self):
        response = self._patch({'youtube_url': 'https://www.youtube.com/watch?v=abcdefghijk', 'transcript': '新しい字幕'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        detail = EventDetail.objects.get(pk=self.detail.pk)
        self.assertEqual(detail.cached_transcript_video_id, 'abcdefghijk')

    def test_refuses_transcript_without_youtube_video(self):
        EventDetail.objects.filter(pk=self.detail.pk).update(youtube_url='')

        response = self._patch({'transcript': '字幕'})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(EventDetail.objects.get(pk=self.detail.pk).cached_transcript, '')

    def _register_while_waiting(self):
        """別リクエストが字幕を登録した状態を、外部 API のモック内で作る。"""
        response = self._patch({'transcript': '録画側で訂正した字幕'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.registered = EventDetail.objects.get(pk=self.detail.pk)

    def _llm_registering_transcript(self):
        client = MagicMock()
        tool_call = MagicMock()
        tool_call.function.arguments = json.dumps(
            {'title': '古い字幕の記事', 'meta_description': '古い要約', 'text': '古い本文'})
        completion = MagicMock()
        completion.choices = [MagicMock(message=MagicMock(tool_calls=[tool_call]))]

        def respond(*args, **kwargs):
            self._register_while_waiting()
            return completion

        client.chat.completions.create.side_effect = respond
        return client

    @patch.dict('os.environ', {'OPENROUTER_API_KEY': 'test-key'}, clear=False)
    @patch('event.services.content_generation_service.OpenAI')
    @patch('event.services.content_generation_service.ensure_pdf_thumbnail')
    def test_manual_generation_keeps_transcript_registered_while_waiting(self, thumbnail, openai_class):
        """LLM 待ち中の字幕 PATCH を無視して記事を保存したり、生成待ちを消したりしない。"""
        EventDetail.objects.filter(pk=self.detail.pk).update(article_consent=EventDetail.ArticleConsent.OK)
        self._patch({'transcript': '生成開始時の字幕'})
        openai_class.return_value = self._llm_registering_transcript()
        web_client = Client()
        web_client.force_login(self.owner)

        with self.assertLogs('event.services.content_generation_service', level='INFO') as logs:
            response = web_client.post(reverse('event:generate_blog', kwargs={'pk': self.detail.pk}))

        self.assertEqual(response.status_code, 302)
        self.assertIn(ARTICLE_EDITED_MESSAGE, [str(message) for message in get_messages(response.wsgi_request)])
        self.assertTrue(any(record.event_type == 'article_generation_transcript_changed'
                            for record in logs.records if hasattr(record, 'event_type')))
        self.detail.refresh_from_db()
        self.assertEqual((self.detail.h1, self.detail.contents, self.detail.meta_description), ('', '', ''))
        self.assertIsNone(self.detail.article_generated_at)
        self.assertEqual(self.detail.cached_transcript, '録画側で訂正した字幕')
        self.assertIsNotNone(self.detail.article_generation_requested_at)
        self.assertEqual(self.detail.article_generation_requested_at, self.registered.article_generation_requested_at)
        self.assertEqual(self.detail.article_source_video_id, '')
        thumbnail.assert_not_called()

    @patch('event.services.content_generation_service.get_transcript')
    def test_youtube_fetch_does_not_overwrite_transcript_registered_while_waiting(self, get_transcript):
        """YouTube 問い合わせ中の字幕 PATCH を条件付き UPDATE で保護する。"""
        def respond(*args, **kwargs):
            self._register_while_waiting()
            return 'YouTube から取得した字幕'

        get_transcript.side_effect = respond
        for cached_text, cached_video in (
            ('', ''), ('', 'QDYMKH1hbbA'), ('録画側で訂正した字幕', 'abcdefghijk'),
        ):
            with self.subTest(cached_text=cached_text):
                EventDetail.objects.filter(pk=self.detail.pk).update(
                    article_consent=EventDetail.ArticleConsent.OK,
                    cached_transcript=cached_text, cached_transcript_video_id=cached_video)
                started = EventDetail.objects.get(pk=self.detail.pk)

                used = fetch_transcript(started)

                # 競り負けた取得結果は捨て、先に登録された字幕で記事を作る
                self.assertEqual(used, '録画側で訂正した字幕')
                self.detail.refresh_from_db()
                self.assertEqual(self.detail.cached_transcript, '録画側で訂正した字幕')
                self.assertEqual(self.detail.cached_transcript_video_id, 'QDYMKH1hbbA')
                self.assertEqual(self.detail.article_generation_requested_at,
                                 self.registered.article_generation_requested_at)
                # 使った字幕と保存時の比較対象をそろえる（登録された字幕で作った記事は保存してよい）
                self.assertEqual((started.cached_transcript, started.cached_transcript_video_id),
                                 ('録画側で訂正した字幕', 'QDYMKH1hbbA'))

    @patch.dict('os.environ', {'OPENROUTER_API_KEY': 'test-key'}, clear=False)
    @patch('event.services.content_generation_service.OpenAI')
    @patch('event.services.article_generation._after_generated')
    def test_queue_preserves_new_transcript_and_its_request(self, after_generated, openai_class):
        """キュー経路は字幕登録が更新したリースを検知し、古い記事を保存しない。"""
        EventDetail.objects.filter(pk=self.detail.pk).update(
            article_consent=EventDetail.ArticleConsent.OK,
            cached_transcript='生成開始時の字幕', cached_transcript_video_id='QDYMKH1hbbA',
            article_generation_requested_at=timezone.now() - timedelta(minutes=1))
        openai_class.return_value = self._llm_registering_transcript()

        result = process_article_generation_queue()

        self.assertEqual(result['results'][0]['reason'], 'superseded')
        self.assertEqual(result['generated'], 0)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.h1, '')
        self.assertEqual(self.detail.cached_transcript, '録画側で訂正した字幕')
        self.assertIsNotNone(self.detail.article_generation_requested_at)
        self.assertEqual(self.detail.article_generation_requested_at, self.registered.article_generation_requested_at)
        after_generated.assert_not_called()
