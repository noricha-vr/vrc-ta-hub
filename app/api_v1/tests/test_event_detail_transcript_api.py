"""API キーで発表に字幕を登録し、記事生成の字幕キャッシュに入ることのテスト。"""

from django.db.models.signals import post_save
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from event.models import EventDetail
from event.services.content_generation_service import fetch_transcript
from tests.factories import make_community, make_event, make_event_detail, make_user
from user_account.models import APIKey


class EventDetailTranscriptAPITest(TestCase):
    def setUp(self):
        self.client = APIClient()
        owner = make_user(user_name='transcript_owner', email='transcript_owner@example.com')
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
