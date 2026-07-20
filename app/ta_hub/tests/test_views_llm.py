from datetime import date, time
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.utils import OperationalError
from django.test import TestCase
from django.urls import reverse

from community.models import Community
from event.models import Event, EventDetail


def _create_test_image():
    """テスト用の最小 PNG バイナリを返す。"""
    import struct
    import zlib

    def _chunk(chunk_type, data):
        chunk = chunk_type + data
        crc = struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + chunk + crc

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw_data = b"\x00\xff\x00\x00"
    idat_data = zlib.compress(raw_data)
    return signature + _chunk(b"IHDR", ihdr_data) + _chunk(b"IDAT", idat_data) + _chunk(b"IEND", b"")


class LlmMarkdownViewTest(TestCase):
    def setUp(self):
        cache.clear()
        self.today = date(2026, 7, 21)
        self.community = Community.objects.create(
            name='Markdown 検証集会',
            start_time=time(21, 0),
            duration=60,
            weekdays=['Tue'],
            frequency='毎週',
            organizers='Markdown Tester',
            status='approved',
            poster_image=SimpleUploadedFile(
                'markdown.png',
                _create_test_image(),
                content_type='image/png',
            ),
        )
        self.event = Event.objects.create(
            community=self.community,
            date=self.today,
            start_time=time(21, 0),
            duration=60,
            weekday='Tue',
        )
        self.detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            speaker='Markdown Speaker',
            theme='Markdown 発表',
            status='approved',
            start_time=time(21, 15),
        )
        self.special = EventDetail.objects.create(
            event=self.event,
            detail_type='SPECIAL',
            h1='Markdown 特別企画',
            status='approved',
            start_time=time(21, 45),
        )

    def tearDown(self):
        cache.clear()

    def test_llms_txt_returns_markdown_content_type(self):
        response = self.client.get(reverse('ta_hub:llms_txt'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/markdown; charset=utf-8')

    def test_llms_txt_body_contains_landmark_sections(self):
        response = self.client.get(reverse('ta_hub:llms_txt'))

        self.assertContains(response, '# VRC技術学術ハブ')
        self.assertContains(response, '## 主要ページ')
        self.assertContains(response, '## 構造化データAPI')
        self.assertContains(response, '## Optional')

    @patch('ta_hub.views_llm.get_vrchat_today')
    def test_index_md_returns_markdown_content_type(self, mock_get_vrchat_today):
        mock_get_vrchat_today.return_value = self.today

        response = self.client.get(reverse('ta_hub:index_md'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/markdown; charset=utf-8')

    @patch('ta_hub.views_llm.get_vrchat_today')
    def test_index_md_lists_upcoming_events(self, mock_get_vrchat_today):
        mock_get_vrchat_today.return_value = self.today

        response = self.client.get(reverse('ta_hub:index_md'))

        self.assertContains(response, '## 今週の発表 (1件)')
        self.assertContains(response, '「Markdown 発表」 by Markdown Speaker')
        self.assertContains(response, '## 今週の集会 (1件)')
        self.assertContains(response, '[Markdown 検証集会](/community/')
        self.assertContains(response, '## 特別企画')
        self.assertContains(response, '[Markdown 特別企画](/event/detail/')

    @patch('ta_hub.views_llm.get_vrchat_today')
    def test_index_md_excludes_records_after_this_week(self, mock_get_vrchat_today):
        mock_get_vrchat_today.return_value = self.today
        future_event = Event.objects.create(
            community=self.community,
            date=date(2026, 7, 29),
            start_time=time(21, 0),
            duration=60,
            weekday='Wed',
        )
        EventDetail.objects.create(
            event=future_event,
            detail_type='LT',
            speaker='Future Speaker',
            theme='Future 発表',
            status='approved',
            start_time=time(21, 15),
        )
        EventDetail.objects.create(
            event=future_event,
            detail_type='SPECIAL',
            h1='Future 特別企画',
            status='approved',
            start_time=time(21, 45),
        )

        response = self.client.get(reverse('ta_hub:index_md'))

        self.assertNotContains(response, 'Future 発表')
        self.assertNotContains(response, 'Future 特別企画')

    @patch('ta_hub.views_llm.get_vrchat_today')
    def test_index_md_escapes_user_content_from_markdown_syntax(self, mock_get_vrchat_today):
        mock_get_vrchat_today.return_value = self.today
        self.community.name = '安全な集会](https://attacker.example)'
        self.community.save(update_fields=['name'])
        self.detail.theme = '通常の題名\n## 偽見出し'
        self.detail.speaker = '発表者 [偽リンク](https://attacker.example)'
        self.detail.save(update_fields=['theme', 'speaker'])

        response = self.client.get(reverse('ta_hub:index_md'))
        body = response.content.decode('utf-8')

        self.assertNotIn('\n## 偽見出し', body)
        self.assertIn('通常の題名 \\#\\# 偽見出し', body)
        self.assertIn('安全な集会\\]\\(https://attacker\\.example\\)', body)
        self.assertIn('発表者 \\[偽リンク\\]\\(https://attacker\\.example\\)', body)

    @patch('ta_hub.views_llm.get_vrchat_today')
    @patch('ta_hub.views.get_vrchat_today')
    @patch('ta_hub.views.Post.objects.filter')
    def test_index_md_uses_same_cache_as_index_html(
        self,
        mock_post_filter,
        mock_html_get_vrchat_today,
        mock_markdown_get_vrchat_today,
    ):
        mock_html_get_vrchat_today.return_value = self.today
        mock_markdown_get_vrchat_today.return_value = self.today
        mock_post_filter.return_value.only.return_value = []

        html_response = self.client.get(reverse('ta_hub:index'))
        self.assertEqual(html_response.status_code, 200)

        with self.assertNumQueries(0):
            markdown_response = self.client.get(reverse('ta_hub:index_md'))

        self.assertEqual(markdown_response.status_code, 200)
        self.assertContains(markdown_response, 'Markdown 発表')

    @patch('ta_hub.views.Event.objects.filter')
    @patch('ta_hub.views_llm.get_vrchat_today')
    def test_index_md_degrades_gracefully_on_db_error(
        self,
        mock_get_vrchat_today,
        mock_event_filter,
    ):
        mock_get_vrchat_today.return_value = self.today
        mock_event_filter.side_effect = OperationalError('db unavailable')

        response = self.client.get(reverse('ta_hub:index_md'))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['database_degraded'])
        self.assertEqual(response.context['upcoming_events'], [])
        self.assertEqual(response.context['upcoming_event_details'], [])
        self.assertEqual(response.context['special_events'], [])

    def test_robots_explicitly_allows_markdown_endpoints(self):
        response = self.client.get('/robots.txt')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Allow: /llms.txt')
        self.assertContains(response, 'Allow: /index.md')
