"""スライド読み込みの取得境界・保存・公開を検証する。外部通信はモックする。"""
from datetime import timedelta
from io import BytesIO
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch

import requests
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfWriter

from api_v1.serializers import EventDetailSerializer
from event.forms import EventDetailForm, LTApplicationEditForm
from event.models import EventDetail, SLIDE_FETCH_FIELDS
from event.services import slide_source
from event.services.content_generation_service import collect_blog_sources, _slide_link_instruction
from event.services.media_service import ensure_pdf_thumbnail
from event.services.slide_source import (
    SlideFetchError, fetch_slide_pdf, is_importable, process_slide_fetch_queue, slide_service,
)
from tests.factories import make_community, make_discord_linked_user, make_event, make_event_detail

PAGE_URL = 'https://speakerdeck.com/example/talk'
NEXT_URL = 'https://speakerdeck.com/example/next'
PDF_URL = 'https://files.speakerdeck.com/presentations/abc/slides.pdf'


def pdf_bytes(pages=2):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=400, height=300)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def uploaded_pdf(name='slides.pdf'):
    return SimpleUploadedFile(name, pdf_bytes(), content_type='application/pdf')


def response(body=b'', status=200, headers=None, chunks=None):
    result = MagicMock()
    result.__enter__.return_value = result
    result.status_code = status
    result.headers = headers or {}
    result.iter_content.return_value = chunks if chunks is not None else [body]
    return result


class SlideServiceTests(SimpleTestCase):
    def test_service_hosts_are_exact(self):
        for url, expected in [
            (PAGE_URL, 'speakerdeck'),
            ('https://www.slideshare.net/slideshow/talk/123', 'slideshare'),
            ('https://slideshare.net/talk', 'slideshare'),
            ('https://www.docswell.com/s/user/123', 'docswell'),
            ('https://docswell.com/s/user/123', 'docswell'),
            ('https://docs.google.com/presentation/d/123/edit', 'google_slides'),
            ('https://speakerdeck.com.evil.invalid/a/b', None),
            ('https://evil.speakerdeck.com/a/b', None),
            ('https://evil.invalid@speakerdeck.com/a/b', None),
            ('https://speakerdeck.com@evil.invalid/a/b', None),
            ('https://speakerdeck.com:443/a/b', None),
            ('not a URL', None), ('https://[', None), ('', None), (None, None),
        ]:
            with self.subTest(url=url):
                self.assertEqual(slide_service(url), expected)

    def test_trailing_slash_is_importable_and_reads_the_canonical_page(self):
        self.assertTrue(is_importable(f'{PAGE_URL}/'))
        self.assertFalse(is_importable(f'{PAGE_URL}//'))
        self.assertEqual(slide_source.canonical_page_url(f'{PAGE_URL}/?slide=3'), PAGE_URL)

    def test_importable_boundary(self):
        for url in [PAGE_URL, PAGE_URL + '?from=hub', 'https://SPEAKERDECK.COM/user/slug']:
            with self.subTest(url=url):
                self.assertTrue(is_importable(url))
        for url in [
            'http://speakerdeck.com/a/b', 'https://www.speakerdeck.com/a/b',
            'https://speakerdeck.com.evil.invalid/a/b', 'https://speakerdeck.com@evil.invalid/a/b',
            'https://name:pass@speakerdeck.com/a/b', 'https://speakerdeck.com:443/a/b',
            'https://speakerdeck.com:/a/b', 'https://speakerdeck.com/a',
            'https://speakerdeck.com/a/b/c',
            'https://speakerdeck.com//b', 'https://speakerdeck.com/a/%2f',
            'https://speakerdeck.com/a/..', 'https://www.slideshare.net/a/b', '', None,
        ]:
            with self.subTest(url=url):
                self.assertFalse(is_importable(url))

    @patch('event.services.slide_source.requests.get')
    def test_downloads_only_the_named_anchor_and_counts_pages(self, get):
        get.side_effect = [
            response(f'<a href="https://evil.invalid/file.pdf">other</a><a title="Download PDF" href="{PDF_URL}">PDF</a>'.encode()),
            response(pdf_bytes(2)),
        ]
        file, pages = fetch_slide_pdf(PAGE_URL)
        self.assertEqual(pages, 2)
        self.assertTrue(file.read().startswith(b'%PDF-'))
        self.assertEqual([c.args[0] for c in get.call_args_list], [PAGE_URL, PDF_URL])
        for call in get.call_args_list:
            self.assertFalse(call.kwargs['allow_redirects'])
            self.assertTrue(call.kwargs['stream'])
            self.assertEqual(call.kwargs['timeout'], (5, 15))
            self.assertEqual(call.kwargs['headers']['User-Agent'], slide_source.USER_AGENT)

    @patch('event.services.slide_source.FETCH_DEADLINE_SECONDS', -1)
    @patch('event.services.slide_source.requests.get')
    def test_slow_response_stops_at_the_overall_deadline(self, get):
        get.return_value = response(b'<html></html>')
        with self.assertRaises(SlideFetchError) as error:
            fetch_slide_pdf(PAGE_URL)
        self.assertEqual(error.exception.reason, 'network')

    @patch('event.services.slide_source.requests.get')
    def test_japanese_file_name_is_percent_encoded_and_allowed(self, get):
        url = 'https://files.speakerdeck.com/presentations/125c6debfa47/%E8%A3%BD%E9%80%A0%E6%A5%AD04.pdf'
        get.side_effect = [
            response(f'<a title="Download PDF" class="text-white" href="{url}">PDF</a>'.encode()),
            response(pdf_bytes(1)),
        ]
        fetch_slide_pdf(PAGE_URL)
        self.assertEqual(get.call_args_list[1].args[0], url)

    @patch('event.services.slide_source.requests.get')
    def test_trailing_slash_and_query_read_the_canonical_page(self, get):
        get.side_effect = [
            response(f'<a title="Download PDF" href="{PDF_URL}">PDF</a>'.encode()),
            response(pdf_bytes(1)),
        ]
        fetch_slide_pdf(f'{PAGE_URL}/?slide=3')
        self.assertEqual(get.call_args_list[0].args[0], PAGE_URL)

    @patch('event.services.slide_source.requests.get')
    def test_rejects_outside_pdf_hrefs_without_connecting(self, get):
        for href in [
            'https://evil.invalid/presentations/abc.pdf',
            'https://files.speakerdeck.com.evil.invalid/presentations/abc.pdf',
            'https://evilfiles.speakerdeck.com/presentations/abc.pdf',
            'https://speakerdeck.com/presentations/abc.pdf',
            'http://files.speakerdeck.com/presentations/abc.pdf',
            'https://evil@files.speakerdeck.com/presentations/abc.pdf',
            'https://files.speakerdeck.com:443/presentations/abc.pdf',
            'https://files.speakerdeck.com/other/abc.pdf',
            'https://files.speakerdeck.com/presentations/../abc.pdf',
            'https://files.speakerdeck.com/presentations/abc.html',
            'https://files.speakerdeck.com/presentations/abc.pdf',
            'https://files.speakerdeck.com/presentations/abc/slides.pdf?x=1',
            'https://files.speakerdeck.com/presentations/abc/%2e%2e%2fsecret.pdf',
            'https://files.speakerdeck.com/presentations/abc/a%5cb.pdf',
            'https://files.speakerdeck.com/presentations/abc/a%00b.pdf',
            'https://files.speakerdeck.com/presentations/%2e%2e/slides.pdf',
            '//files.speakerdeck.com/presentations/abc.pdf',
        ]:
            with self.subTest(href=href):
                get.reset_mock()
                get.return_value = response(f'<a title="Download PDF" href="{href}">PDF</a>'.encode())
                with self.assertRaises(SlideFetchError) as error:
                    fetch_slide_pdf(PAGE_URL)
                self.assertEqual(error.exception.reason, 'no_pdf_link')
                get.assert_called_once()

    @patch('event.services.slide_source.requests.get')
    def test_redirects_are_not_followed_on_either_request(self, get):
        for responses in [
            [response(status=302, headers={'Location': 'https://evil.invalid/'})],
            [response(f'<a title="Download PDF" href="{PDF_URL}">PDF</a>'.encode()), response(status=302)],
        ]:
            with self.subTest(requests=len(responses)):
                get.reset_mock()
                get.side_effect = responses
                with self.assertRaises(SlideFetchError) as error:
                    fetch_slide_pdf(PAGE_URL)
                self.assertEqual(error.exception.reason, 'page_unavailable')
                self.assertEqual(get.call_count, len(responses))

    @patch('event.services.slide_source.requests.get')
    def test_stream_is_stopped_at_the_limit(self, get):
        consumed = []
        def chunks():
            for part in [b'1234', b'5678', b'never-read']:
                consumed.append(part)
                yield part
        get.return_value = response(chunks=chunks())
        with patch.object(slide_source, 'MAX_HTML_BYTES', 5):
            with self.assertRaises(SlideFetchError) as error:
                fetch_slide_pdf(PAGE_URL)
        self.assertEqual(error.exception.reason, 'too_large')
        self.assertEqual(consumed, [b'1234', b'5678'])
        get.return_value.__exit__.assert_called_once()

    @patch('event.services.slide_source.requests.get')
    def test_content_length_and_pdf_limits(self, get):
        oversized = response(headers={'Content-Length': str(slide_source.MAX_HTML_BYTES + 1)})
        get.return_value = oversized
        with self.assertRaises(SlideFetchError) as error:
            fetch_slide_pdf(PAGE_URL)
        self.assertEqual(error.exception.reason, 'too_large')
        oversized.iter_content.assert_not_called()
        get.side_effect = [response(f'<a title="Download PDF" href="{PDF_URL}">'.encode()), response(chunks=[b'123456'])]
        with patch.object(slide_source, 'MAX_PDF_SIZE_BYTES', 5):
            with self.assertRaises(SlideFetchError) as error:
                fetch_slide_pdf(PAGE_URL)
        self.assertEqual(error.exception.reason, 'too_large')

    @patch('event.services.slide_source.requests.get')
    def test_no_link_invalid_pdf_and_network_reasons(self, get):
        cases = [
            ([response(b'<html>no PDF</html>')], 'no_pdf_link'),
            ([response(f'<a title="Download PDF" href="{PDF_URL}">'.encode()), response(b'<html>not PDF</html>')], 'invalid_pdf'),
            ([response(f'<a title="Download PDF" href="{PDF_URL}">'.encode()), response(b'%PDF-1.7\nbroken')], 'invalid_pdf'),
            ([response(status=404)], 'page_unavailable'),
            ([response(status=503)], 'network'),
            ([requests.Timeout('full URL must not be recorded')], 'network'),
        ]
        for responses, reason in cases:
            with self.subTest(reason=reason):
                get.side_effect = responses
                with self.assertRaises(SlideFetchError) as error:
                    fetch_slide_pdf(PAGE_URL)
                self.assertEqual(error.exception.reason, reason)

    @patch('event.services.slide_source.requests.get')
    def test_non_importable_page_never_connects(self, get):
        with self.assertRaises(SlideFetchError):
            fetch_slide_pdf('https://www.slideshare.net/slideshow/a/123')
        get.assert_not_called()


class SlideSourceTestCase(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.media_root = directory.name
        self.settings_override = override_settings(MEDIA_ROOT=directory.name, DISCORD_AUTH_REQUIRED=False)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.source_storage = FileSystemStorage(location=Path(directory.name) / 'private')
        storage_patch = patch.object(EventDetail._meta.get_field('slide_source_file'), 'storage', self.source_storage)
        storage_patch.start()
        self.addCleanup(storage_patch.stop)
        self.owner = make_discord_linked_user(user_name='slide-owner', email='owner@example.com')
        self.speaker = make_discord_linked_user(user_name='slide-speaker', email='speaker@example.com')
        self.other = make_discord_linked_user(user_name='slide-other', email='other@example.com')
        self.community = make_community(owner=self.owner)
        self.event = make_event(self.community, event_date=timezone.localdate())
        self.detail = make_event_detail(self.event, applicant=self.speaker, article_consent='ok', status='approved')
        for target in [
            'event.services.media_service.ensure_pdf_thumbnail',
            'event.services.slide_source.ensure_pdf_thumbnail',
            'twitter.services.tweet_generation.sync_slide_share_queue_image',
        ]:
            mock = patch(target)
            mock.start()
            self.addCleanup(mock.stop)

    def pending(self, **kwargs):
        values = {'slide_url': PAGE_URL, 'slide_fetch_url': PAGE_URL, 'slide_fetch_status': 'pending', **kwargs}
        EventDetail.all_objects.filter(pk=self.detail.pk).update(**values)
        self.detail.refresh_from_db()
        return self.detail

    def form(self, form_class=EventDetailForm, **data):
        self.detail.refresh_from_db()
        values = {
            'detail_type': 'LT', 'theme': '発表のテーマ', 'speaker': '発表者',
            'start_time': '22:00', 'duration': '30', 'recording_policy': 'public',
            'article_consent': self.detail.article_consent, 'slide_url': self.detail.slide_url or '',
            'generate_blog_article': '', **data,
        }
        files = {name: values.pop(name) for name in ('slide_file', 'slide_source_file') if name in values}
        return form_class(data=values, files=files, instance=self.detail)

    def save_form(self, form):
        self.assertTrue(form.is_valid(), form.errors.as_json())
        with self.captureOnCommitCallbacks(execute=True):
            detail = form.save()
        detail.refresh_from_db()
        return detail

    def put_source(self, *, auto=True):
        self.detail.slide_source_file.save('source.pdf', ContentFile(pdf_bytes()), save=False)
        self.detail.slide_fetch_url = PAGE_URL if auto else ''
        self.detail.slide_url = PAGE_URL
        self.detail.slide_fetch_status = 'success' if auto else ''
        self.detail.save(update_fields=SLIDE_FETCH_FIELDS + ('slide_url',))
        return self.detail.slide_source_file.name


class SlideFetchQueueTests(SlideSourceTestCase):
    def test_claim_is_conditional_and_expired_lease_is_reclaimed(self):
        self.pending()
        now = timezone.now()
        self.assertEqual(slide_source._claim_next(now, []), (self.detail.pk, now))
        self.assertIsNone(slide_source._claim_next(now, []))
        self.assertEqual(slide_source._claim_next(now + slide_source.CLAIM_LEASE + timedelta(seconds=1), [])[0], self.detail.pk)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.slide_fetch_attempts, 2)

    @patch('event.services.slide_source.fetch_slide_pdf', side_effect=RuntimeError('boom'))
    def test_unexpected_error_does_not_leave_fetching_and_stops_after_three(self, fetch):
        detail = self.pending()
        for expected in ('pending', 'pending', 'failed'):
            process_slide_fetch_queue()
            detail.refresh_from_db()
            self.assertEqual(detail.slide_fetch_status, expected)
            self.assertEqual(detail.slide_fetch_error, 'internal')
            self.assertIsNone(detail.slide_fetch_started_at)
        process_slide_fetch_queue()
        self.assertEqual(fetch.call_count, 3)

    @patch('event.services.slide_source.fetch_slide_pdf')
    def test_expired_lease_over_the_limit_fails_without_reading(self, fetch):
        detail = self.pending()
        EventDetail.objects.filter(pk=detail.pk).update(
            slide_fetch_status='fetching', slide_fetch_attempts=3,
            slide_fetch_started_at=timezone.now() - timedelta(minutes=6),
        )
        process_slide_fetch_queue()
        detail.refresh_from_db()
        self.assertEqual(detail.slide_fetch_status, 'failed')
        fetch.assert_not_called()

    @patch('event.services.slide_source.ensure_pdf_thumbnail', side_effect=RuntimeError('boom'))
    @patch('event.services.slide_source.fetch_slide_pdf')
    def test_thumbnail_failure_keeps_success(self, fetch, thumbnail):
        fetch.return_value = (ContentFile(pdf_bytes(), name='source.pdf'), 2)
        detail = self.pending()
        process_slide_fetch_queue()
        detail.refresh_from_db()
        self.assertEqual(detail.slide_fetch_status, 'success')

    @patch('event.services.slide_source.fetch_slide_pdf')
    def test_lost_claim_does_not_read_the_page(self, fetch):
        self.pending()
        claimed = slide_source._claim_next(timezone.now(), [])
        EventDetail.all_objects.filter(pk=self.detail.pk).update(
            slide_fetch_status='pending', slide_fetch_started_at=None,
        )
        self.assertEqual(slide_source._process_one(*claimed)['outcome'], 'skipped')
        fetch.assert_not_called()

    @patch('event.services.slide_source.fetch_slide_pdf')
    def test_success_saves_non_public_pdf_and_marks_article_queue(self, fetch):
        self.pending()
        fetch.return_value = (ContentFile(pdf_bytes(), name='source.pdf'), 2)
        with self.assertLogs('event.services.slide_source', level='INFO') as logs:
            result = process_slide_fetch_queue()
        self.detail.refresh_from_db()
        self.assertEqual(result['results'][0]['outcome'], 'success')
        self.assertTrue(self.detail.slide_source_file.name.startswith('slide-source/'))
        self.assertFalse(self.detail.slide_file)
        self.assertEqual(self.detail.slide_source_page_count, 2)
        self.assertEqual(self.detail.slide_fetch_status, 'success')
        self.assertIsNotNone(self.detail.slide_fetched_at)
        self.assertIsNotNone(self.detail.article_generation_requested_at)
        self.assertEqual(self.detail.article_sources()[1], self.detail.slide_source_file.name)
        self.assertNotIn(PAGE_URL, str(logs.output))
        slide_source.ensure_pdf_thumbnail.assert_called_once()

    @patch('event.services.slide_source.fetch_slide_pdf', side_effect=SlideFetchError('network'))
    def test_network_retries_on_later_calls_and_stops_after_three(self, fetch):
        self.pending()
        for attempt in range(1, 4):
            result = process_slide_fetch_queue(limit=2)
            self.assertEqual(result['processed'], 1)
            self.detail.refresh_from_db()
            self.assertEqual(self.detail.slide_fetch_attempts, attempt)
            self.assertEqual(self.detail.slide_fetch_status, 'pending' if attempt < 3 else 'failed')
        self.assertEqual(process_slide_fetch_queue()['processed'], 0)
        self.assertEqual(fetch.call_count, 3)

    @patch('event.services.slide_source.fetch_slide_pdf')
    def test_permanent_errors_are_not_retried(self, fetch):
        for reason in ['no_pdf_link', 'page_unavailable', 'too_large', 'invalid_pdf']:
            self.pending(slide_fetch_attempts=0)
            fetch.side_effect = SlideFetchError(reason)
            process_slide_fetch_queue()
            self.detail.refresh_from_db()
            self.assertEqual(self.detail.slide_fetch_status, 'failed')
            self.assertEqual(self.detail.slide_fetch_error, reason)
            self.assertEqual(process_slide_fetch_queue()['processed'], 0)

    @patch('event.services.slide_source.fetch_slide_pdf')
    def test_ineligible_rows_are_reset_without_reading(self, fetch):
        for values in [
            {'article_consent': 'ng'}, {'status': 'rejected'}, {'deleted_at': timezone.now()},
            {'slide_url': NEXT_URL},
        ]:
            self.pending(**{'article_consent': 'ok', 'status': 'approved', 'deleted_at': None, **values})
            self.assertEqual(process_slide_fetch_queue()['results'][0]['outcome'], 'skipped')
            self.detail.refresh_from_db()
            self.assertEqual(self.detail.slide_fetch_status, '')
        fetch.assert_not_called()

    @patch('event.services.slide_source.fetch_slide_pdf')
    def test_changed_url_or_consent_during_fetch_is_not_saved(self, fetch):
        for values in [{'slide_url': NEXT_URL}, {'article_consent': 'ng'}, {'deleted_at': timezone.now()}]:
            self.pending(article_consent='ok', deleted_at=None)
            def finish(_url):
                EventDetail.all_objects.filter(pk=self.detail.pk).update(**values)
                return ContentFile(pdf_bytes(), name='source.pdf'), 2
            fetch.side_effect = finish
            self.assertEqual(process_slide_fetch_queue()['results'][0]['outcome'], 'skipped')
            self.detail.refresh_from_db()
            self.assertFalse(self.detail.slide_source_file)

    @patch('event.views.article_generation.process_article_generation_queue', return_value={'processed': 0})
    @patch('event.views.article_generation.process_slide_fetch_queue', side_effect=RuntimeError('private URL'))
    @override_settings(REQUEST_TOKEN='queue-test-token')
    def test_slide_failure_does_not_stop_article_queue(self, fetch, articles):
        result = self.client.get(reverse('event:run_article_generation'), HTTP_REQUEST_TOKEN='queue-test-token')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['slide_fetch']['error'], 'internal')
        articles.assert_called_once()


class SlideEntryFormTests(SlideSourceTestCase):
    def test_related_rows_are_locked_before_the_detail(self):
        order = []
        manager = type(EventDetail.all_objects)
        original = manager.select_for_update

        def lock_detail(*args, **kwargs):
            order.append('detail')
            return original(*args, **kwargs)

        form = self.form()
        self.assertTrue(form.is_valid(), form.errors.as_json())
        with patch('vket.application_link.lock_linked_detail', side_effect=lambda detail: order.append('related')):
            with patch.object(manager, 'select_for_update', autospec=True, side_effect=lock_detail):
                form.save()
        self.assertEqual(order[:2], ['related', 'detail'])

    def test_manual_source_opens_on_link_side_and_survives_the_next_save(self):
        name = self.put_source(auto=False)
        EventDetail.objects.filter(pk=self.detail.pk).update(slide_url='')
        self.detail.refresh_from_db()
        form = self.form()
        self.assertEqual(form.fields['slide_entry'].initial, 'link')
        detail = self.save_form(self.form(slide_entry='link'))
        self.assertEqual(detail.slide_source_file.name, name)

    def test_manual_source_can_be_replaced_on_the_same_screen(self):
        self.put_source(auto=False)
        state = self.form().slide_state
        self.assertEqual(state['chip'], 'PDF 登録済み')
        self.assertTrue(state['fallback'])

    def test_unimportable_link_asks_for_pdf_upload(self):
        EventDetail.objects.filter(pk=self.detail.pk).update(
            slide_url='https://www.slideshare.net/slideshow/example/123',
        )
        self.detail.refresh_from_db()
        state = self.form().slide_state
        self.assertEqual(state['chip'], 'リンクのみ')
        self.assertTrue(state['fallback'])
        self.assertIn('PDF をアップロード', state['message'])

    def test_outdated_source_is_not_used_after_url_changes_outside_the_form(self):
        self.put_source()
        EventDetail.objects.filter(pk=self.detail.pk).update(slide_url=NEXT_URL)
        self.detail.refresh_from_db()
        self.assertFalse(self.detail.article_pdf)

    def test_initial_selection(self):
        self.assertEqual(EventDetailForm(instance=self.detail)['slide_entry'].value(), '')
        self.detail.slide_file = 'slide/example.pdf'
        self.assertEqual(EventDetailForm(instance=self.detail)['slide_entry'].value(), 'pdf')
        self.detail.slide_url = PAGE_URL
        self.assertEqual(EventDetailForm(instance=self.detail)['slide_entry'].value(), 'link')

    def test_link_save_schedules_both_edit_forms(self):
        for form_class in (EventDetailForm, LTApplicationEditForm):
            with self.subTest(form=form_class):
                detail = self.save_form(self.form(form_class, slide_entry='link', slide_url=PAGE_URL))
                self.assertEqual(detail.slide_fetch_status, 'pending')
                self.assertEqual(detail.slide_fetch_url, PAGE_URL)

    def test_no_js_fallback_and_no_fetch_on_open(self):
        self.detail.slide_url = PAGE_URL
        self.detail.save(update_fields=['slide_url'])
        EventDetailForm(instance=self.detail)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.slide_fetch_status, '')
        detail = self.save_form(self.form(slide_url=PAGE_URL))
        self.assertEqual(detail.slide_fetch_status, 'pending')

    def test_pdf_save_removes_link_and_private_file(self):
        name = self.put_source()
        detail = self.save_form(self.form(slide_entry='pdf', slide_url=PAGE_URL, slide_file=uploaded_pdf()))
        self.assertEqual(detail.slide_url, '')
        self.assertFalse(detail.slide_source_file)
        self.assertEqual(detail.slide_fetch_status, '')
        self.assertEqual(detail.slide_fetch_url, '')
        self.assertTrue(detail.slide_file)
        self.assertFalse(self.source_storage.exists(name))

    def test_link_save_preserves_public_pdf_even_if_replacement_was_posted(self):
        self.detail.slide_file.save('public.pdf', ContentFile(pdf_bytes()), save=False)
        self.detail.save(update_fields=['slide_file'])
        name = self.detail.slide_file.name
        detail = self.save_form(self.form(slide_entry='link', slide_url=PAGE_URL, slide_file=uploaded_pdf('ignored.pdf')))
        self.assertEqual(detail.slide_file.name, name)
        self.assertEqual(detail.slide_fetch_status, 'pending')

    def test_manual_article_pdf_does_not_trigger_fetch_or_disappear_on_url_change(self):
        detail = self.save_form(self.form(slide_entry='link', slide_url=PAGE_URL, slide_source_file=uploaded_pdf()))
        name = detail.slide_source_file.name
        self.assertTrue(name)
        self.assertEqual(detail.slide_fetch_url, '')
        self.assertEqual(detail.slide_fetch_status, '')
        detail = self.save_form(self.form(slide_entry='link', slide_url=NEXT_URL))
        self.assertEqual(detail.slide_source_file.name, name)
        self.assertEqual(detail.slide_fetch_status, '')

    def test_automatic_article_pdf_is_replaced_when_url_changes(self):
        name = self.put_source()
        detail = self.save_form(self.form(slide_entry='link', slide_url=NEXT_URL))
        self.assertFalse(detail.slide_source_file)
        self.assertEqual(detail.slide_fetch_status, 'pending')
        self.assertEqual(detail.slide_fetch_url, NEXT_URL)
        self.assertFalse(self.source_storage.exists(name))

    def test_ng_and_other_service_do_not_schedule_fetch(self):
        detail = self.save_form(self.form(LTApplicationEditForm, article_consent='ng', slide_entry='link', slide_url=PAGE_URL))
        self.assertEqual(detail.slide_fetch_status, '')
        detail = self.save_form(self.form(LTApplicationEditForm, article_consent='ok', slide_entry='link', slide_url='https://www.slideshare.net/slideshow/a/123'))
        self.assertEqual(detail.slide_fetch_status, '')

    def test_invalid_manual_pdf_is_rejected(self):
        form = self.form(slide_entry='link', slide_url='https://example.com/talk', slide_source_file=SimpleUploadedFile('bad.pdf', b'not PDF'))
        self.assertFalse(form.is_valid())
        self.assertIn('slide_source_file', form.errors)

    def test_background_fetch_is_preserved_by_old_form(self):
        self.pending()
        form = self.form(slide_entry='link', slide_url=PAGE_URL)
        self.assertTrue(form.is_valid(), form.errors)
        name = self.put_source()
        detail = self.save_form(form)
        self.assertEqual(detail.slide_source_file.name, name)
        self.assertEqual(detail.slide_fetch_status, 'success')

    def test_pdf_mode_clears_private_source_in_applicant_form(self):
        name = self.put_source()
        detail = self.save_form(self.form(LTApplicationEditForm, slide_entry='pdf', slide_file=uploaded_pdf()))
        self.assertEqual(detail.slide_url, '')
        self.assertFalse(detail.slide_source_file)
        self.assertFalse(self.source_storage.exists(name))

    def test_full_save_preserves_new_source_from_background_fetch(self):
        stale = EventDetail.objects.get(pk=self.detail.pk)
        name = self.put_source()
        stale.theme = 'テーマの変更'
        stale.save()
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.slide_source_file.name, name)
        self.assertEqual(self.detail.slide_fetch_status, 'success')

    def test_explicit_source_file_save_still_persists(self):
        self.detail.slide_source_file.save('source.pdf', ContentFile(pdf_bytes()))
        name = self.detail.slide_source_file.name
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.slide_source_file.name, name)

    def test_commit_false_does_not_delete_the_stored_source(self):
        name = self.put_source()
        form = self.form(slide_entry='pdf')
        self.assertTrue(form.is_valid(), form.errors)
        with self.captureOnCommitCallbacks(execute=True):
            form.save(commit=False)
        self.assertTrue(self.source_storage.exists(name))
        self.assertEqual(EventDetail.objects.get(pk=self.detail.pk).slide_source_file.name, name)

    def test_owner_form_does_not_restore_old_consent(self):
        form = self.form(slide_entry='link', slide_url=PAGE_URL)
        self.assertTrue(form.is_valid(), form.errors)
        EventDetail.objects.filter(pk=self.detail.pk).update(article_consent='ng')
        detail = self.save_form(form)
        self.assertEqual(detail.article_consent, 'ng')
        self.assertEqual(detail.slide_fetch_status, '')

    def test_automatic_source_keeps_its_origin_when_consent_changes(self):
        self.put_source()
        detail = self.save_form(self.form(LTApplicationEditForm, article_consent='ng', slide_entry='link'))
        self.assertEqual(detail.slide_fetch_url, PAGE_URL)
        detail = self.save_form(self.form(LTApplicationEditForm, article_consent='ok', slide_entry='link', slide_url=NEXT_URL))
        self.assertFalse(detail.slide_source_file)
        self.assertEqual(detail.slide_fetch_status, 'pending')

    def test_pdf_mode_ignores_invalid_link_and_inactive_private_upload(self):
        detail = self.save_form(self.form(
            slide_entry='pdf', slide_url='not a valid URL', slide_file=uploaded_pdf(),
            slide_source_file=SimpleUploadedFile('bad.pdf', b''),
        ))
        self.assertTrue(detail.slide_file)
        self.assertEqual(detail.slide_url, '')
        self.assertFalse(detail.slide_source_file)

    def test_link_mode_ignores_invalid_public_upload_and_clear_flag(self):
        self.detail.slide_file.save('public.pdf', ContentFile(pdf_bytes()))
        name = self.detail.slide_file.name
        detail = self.save_form(self.form(
            slide_entry='link', slide_url=PAGE_URL, slide_file=SimpleUploadedFile('bad.pdf', b''),
            **{'slide_file-clear': 'on'},
        ))
        self.assertEqual(detail.slide_file.name, name)
        self.assertEqual(detail.slide_fetch_status, 'pending')

    def test_private_pdf_widget_never_renders_url(self):
        self.put_source(auto=False)
        with patch.object(self.source_storage, 'url', side_effect=AssertionError('private URL requested')):
            form = EventDetailForm(instance=self.detail)
            html = render_to_string('event/includes/slide_entry_fields.html', {'form': form})
        self.assertNotIn('href="/media/slide-source/', html)
        self.assertIn('name="slide_source_file"', html)


class SlideThumbnailTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        override = override_settings(MEDIA_ROOT=directory.name)
        override.enable()
        self.addCleanup(override.disable)
        storage = patch.object(
            EventDetail._meta.get_field('slide_source_file'), 'storage',
            FileSystemStorage(location=Path(directory.name) / 'private'),
        )
        storage.start()
        self.addCleanup(storage.stop)
        self.detail = make_event_detail(
            make_event(make_community(), event_date=timezone.localdate()), article_consent='ng',
        )
        self.detail.slide_source_file.save('source.pdf', ContentFile(pdf_bytes()), save=True)

    @patch('event.services.media_service.run_pdf_worker', return_value=b'image')
    def test_private_pdf_does_not_become_a_public_image_when_article_is_refused(self, worker):
        self.assertFalse(ensure_pdf_thumbnail(self.detail, save=True))
        worker.assert_not_called()
        self.detail.refresh_from_db()
        self.assertFalse(self.detail.thumbnail_image)

    @patch('event.services.media_service.run_pdf_worker', return_value=b'image')
    def test_image_is_discarded_when_article_is_refused_while_rendering(self, worker):
        EventDetail.objects.filter(pk=self.detail.pk).update(article_consent='ok')
        self.detail.refresh_from_db()

        def refuse(*args):
            EventDetail.objects.filter(pk=self.detail.pk).update(article_consent='ng')
            return b'image'

        worker.side_effect = refuse
        self.assertFalse(ensure_pdf_thumbnail(self.detail, save=True))
        self.detail.refresh_from_db()
        self.assertFalse(self.detail.thumbnail_image)


class SlideRetryAndPublicTests(SlideSourceTestCase):
    def test_retry_allows_owner_and_speaker_but_not_other(self):
        url = reverse('event:slide_fetch_retry', args=[self.detail.pk])
        for user, allowed in [(self.owner, True), (self.speaker, True), (self.other, False)]:
            self.pending(slide_fetch_status='failed', slide_fetch_attempts=3, slide_fetch_error='network')
            self.client.force_login(user)
            result = self.client.post(url)
            self.assertEqual(result.status_code, 302 if allowed else 403)
            self.detail.refresh_from_db()
            self.assertEqual(self.detail.slide_fetch_status, 'pending' if allowed else 'failed')
            if allowed:
                self.assertEqual(self.detail.slide_fetch_attempts, 0)

    def test_pending_applicant_can_retry_from_own_edit_page(self):
        self.pending(status='pending', slide_fetch_status='failed')
        self.client.force_login(self.speaker)
        result = self.client.post(reverse('event:slide_fetch_retry', args=[self.detail.pk]), {'return_to': 'application'})
        self.assertRedirects(result, reverse('account:lt_application_edit', args=[self.detail.pk]), fetch_redirect_response=False)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.slide_fetch_status, 'pending')

    def test_retry_is_post_only_and_csrf_protected_and_failed_only(self):
        self.client.force_login(self.owner)
        url = reverse('event:slide_fetch_retry', args=[self.detail.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        self.assertEqual(csrf_client.post(url).status_code, 403)
        for status in ('', 'pending', 'fetching', 'success'):
            self.pending(slide_fetch_status=status, slide_fetch_attempts=2)
            self.client.post(url)
            self.detail.refresh_from_db()
            self.assertEqual(self.detail.slide_fetch_status, status)
            self.assertEqual(self.detail.slide_fetch_attempts, 2)

    def test_ng_rejected_deleted_and_non_importable_are_not_retried(self):
        self.client.force_login(self.owner)
        url = reverse('event:slide_fetch_retry', args=[self.detail.pk])
        for values in [{'article_consent': 'ng'}, {'status': 'rejected'}, {'deleted_at': timezone.now()}, {'slide_url': 'https://example.com/talk'}]:
            self.pending(**{'article_consent': 'ok', 'status': 'approved', 'deleted_at': None, 'slide_fetch_status': 'failed', **values})
            self.client.post(url)
            self.detail.refresh_from_db()
            self.assertEqual(self.detail.slide_fetch_status, 'failed')

    def test_public_page_prefers_service_link_and_never_exposes_source(self):
        name = self.put_source()
        self.detail.slide_file.save('public.pdf', ContentFile(pdf_bytes()), save=False)
        self.detail.save(update_fields=['slide_file'])
        with patch.object(self.source_storage, 'url', side_effect=AssertionError('private URL requested')):
            result = self.client.get(reverse('event:detail', args=[self.detail.pk]))
            api_result = self.client.get('/api/v1/event_detail/')
            serialized = EventDetailSerializer(EventDetail.objects.get(pk=self.detail.pk)).data
        self.assertContains(result, 'Speaker Deck でスライドを見る ↗')
        self.assertContains(result, 'data-slide-service="speakerdeck"')
        self.assertNotContains(result, 'スライドの PDF を開く')
        self.assertNotContains(result, name)
        self.assertEqual(api_result.status_code, 200)
        self.assertNotIn(name, api_result.content.decode())
        self.assertNotIn('slide_source_file', serialized)
        self.assertNotIn(name, str(serialized))
        self.detail.slide_url = ''
        self.detail.save(update_fields=['slide_url'])
        self.assertContains(self.client.get(reverse('event:detail', args=[self.detail.pk])), 'スライドの PDF を開く')

    def test_uploaded_pdf_never_overrides_pasted_link(self):
        """読み込めないリンクでも、貼った URL を公開ページの案内先にする。"""
        url = 'https://www.slideshare.net/slideshow/example/123'
        self.detail.slide_url = url
        self.detail.slide_file.save('public.pdf', ContentFile(pdf_bytes()), save=False)
        self.detail.slide_source_file.save('manual.pdf', ContentFile(pdf_bytes()), save=False)
        self.detail.save(update_fields=['slide_url', 'slide_file', 'slide_source_file'])
        result = self.client.get(reverse('event:detail', args=[self.detail.pk]))
        self.assertContains(result, 'SlideShare でスライドを見る ↗')
        self.assertContains(result, f'href="{url}"')
        self.assertNotContains(result, 'スライドの PDF を開く')
        self.assertEqual(self.detail.article_pdf.name, self.detail.slide_source_file.name)

    def test_public_unknown_service_label(self):
        self.detail.slide_url = 'https://example.com/slides'
        self.detail.save(update_fields=['slide_url'])
        self.assertContains(self.client.get(reverse('event:detail', args=[self.detail.pk])), '発表スライドを開く ↗')

    def test_blog_uses_private_pdf_but_only_public_link(self):
        self.put_source()
        with (
            patch('event.services.content_generation_service._read_slide_text', return_value='slide text'),
            patch.object(self.source_storage, 'url', side_effect=AssertionError('private URL requested')),
        ):
            sources = collect_blog_sources(self.detail, transcript='')
        self.assertEqual(sources.pdf_url, PAGE_URL)
        self.assertEqual(sources.slide_name, self.detail.slide_source_file.name)
        self.assertIn('[発表スライド](', _slide_link_instruction(PAGE_URL, is_external=True))
