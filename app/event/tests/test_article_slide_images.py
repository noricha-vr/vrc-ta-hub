"""記事のページ選択・画像挿入・公開画像の制約。LLM と PDF ワーカーはモックする。"""
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

import event_pdf_worker
from event.models import EventDetail
from event.services import pdf_worker
from event.services.article_slide_images import MAX_ARTICLE_SLIDE_IMAGES
from event.services.content_generation_service import BlogSources, collect_blog_sources, generate_blog
from event.services.markdown_processor import _sanitize_html, convert_markdown
from event.services.pdf_worker import PdfWorkerError, run_pdf_worker
from website.constants import SITE_DOMAIN


@override_settings(MEDIA_URL='/media/')
class ArticleSlideImagesTests(SimpleTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        override = override_settings(MEDIA_ROOT=directory.name)
        override.enable()
        self.addCleanup(override.disable)
        self.detail = EventDetail(
            theme='発表', speaker='登壇者', youtube_url='https://youtu.be/abcdefghijk',
            article_consent='ok',
        )
        # DB・PDF ライブラリ・外部 API に依存しない生成経路のテスト。
        self.detail._state.fields_cache['event'] = SimpleNamespace(
            date='2026-11-18', community=SimpleNamespace(name='技術集会'),
        )
        self.detail.slide_file = SimpleUploadedFile('slides.pdf', b'PDF fixture')
        self.sources = BlogSources('', '[ページ 1]\n導入\n[ページ 2]\n実践', '', slide_name='slides.pdf')

    def generate(self, text, pages, *, fallback=False):
        response = {'title': '発表記事', 'meta_description': '要約', 'text': text, 'slide_pages': pages}
        message = MagicMock()
        message.tool_calls = [] if fallback else [SimpleNamespace(
            type='function', function=SimpleNamespace(arguments=json.dumps(response)),
        )]
        message.content = json.dumps(response) if fallback else None
        with (
            patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test-only'}),
            patch('event.services.content_generation_service.OpenAI') as client,
        ):
            client.return_value.chat.completions.create.return_value.choices = [SimpleNamespace(message=message)]
            result = generate_blog(self.detail, sources=self.sources)
        self.assertEqual(result.title, '発表記事')
        self.prompt = client.return_value.chat.completions.create.call_args.kwargs['messages'][1]['content']
        return result

    @patch('event.services.article_slide_images.run_pdf_worker', return_value=b'jpeg')
    def test_selected_pages_are_inserted_immediately_after_h2_and_null_is_omitted(self, worker):
        for fallback in (False, True):
            with self.subTest(fallback=fallback):
                self.detail.slide_file = SimpleUploadedFile('slides.pdf', b'PDF fixture')
                result = self.generate('## 導入\n\n説明\n\n## まとめ\n\n結論', [
                    {'heading': '導入', 'page': 2}, {'heading': 'まとめ', 'page': None},
                ], fallback=fallback)
                self.assertRegex(result.text, r'## 導入\n\n!\[導入のスライド\]\(/media/slide_page/[\w-]+/2.jpg\)')
                soup = BeautifulSoup(convert_markdown(result.text), 'html.parser')
                self.assertEqual(soup.h2.find_next_sibling().img['alt'], '導入のスライド')
                self.assertEqual(len(soup.find_all('img')), 1)
                self.assertIn('[ページ 2]', self.prompt)
                self.assertIn('PDFの内容を画像として記事に埋めこんではいけません', self.prompt)
                self.assertEqual(worker.call_args.kwargs, {'page': 2})
                image_files = list(Path(self._media_root()).glob('slide_page/*/2.jpg'))
                self.assertTrue(image_files)

    @staticmethod
    def _media_root():
        from django.conf import settings
        return settings.MEDIA_ROOT

    @patch('event.services.article_slide_images.run_pdf_worker')
    def test_without_pdf_generates_article_without_images(self, worker):
        self.detail.slide_file = ''
        self.sources = BlogSources('字幕', '', '')
        result = self.generate('## 導入\n\n本文', [{'heading': '導入', 'page': 1}])
        self.assertNotIn('![', result.text)
        worker.assert_not_called()

    @patch('event.services.article_slide_images.run_pdf_worker')
    def test_invalid_pages_and_failed_render_leave_the_rest_of_article(self, worker):
        worker.side_effect = lambda operation, path, *, page: (
            b'jpeg' if page == 2 else self.fail_page(operation)
        )
        result = self.generate('## 範囲外\n\n本文\n\n## 失敗\n\n本文\n\n## 成功\n\n本文\n\n## 負数', [
            {'heading': '範囲外', 'page': 999}, {'heading': '失敗', 'page': 3},
            {'heading': '成功', 'page': 2}, {'heading': '負数', 'page': -1},
        ])
        self.assertEqual(result.text.count('!['), 1)
        self.assertIn('![成功のスライド]', result.text)
        self.assertIn('## 失敗\n\n本文', result.text)
        self.assertEqual([c.kwargs['page'] for c in worker.call_args_list], [3, 2])

    @staticmethod
    def fail_page(operation):
        raise PdfWorkerError(operation, 'worker_error')

    @patch('event.services.article_slide_images.run_pdf_worker', return_value=b'jpeg')
    def test_image_limit_and_unknown_heading(self, worker):
        result = self.generate('\n\n'.join(f'## 見出し{i}\n\n本文' for i in range(12)),
                               [{'heading': '存在しない', 'page': 1}] + [
                                   {'heading': f'見出し{i}', 'page': i + 1} for i in range(12)])
        self.assertEqual(result.text.count('!['), MAX_ARTICLE_SLIDE_IMAGES)
        self.assertEqual(worker.call_count, MAX_ARTICLE_SLIDE_IMAGES)

    @patch('event.services.article_slide_images.time.monotonic', side_effect=[0, 0, 100, 100, 100])
    @patch('event.services.article_slide_images.run_pdf_worker', return_value=b'jpeg')
    def test_total_time_budget_skips_remaining_pages_but_keeps_article(self, worker, _clock):
        result = self.generate('## 一\n\n本文\n\n## 二\n\n本文',
                               [{'heading': '一', 'page': 1}, {'heading': '二', 'page': 2}])
        self.assertEqual(result.text.count('!['), 1)
        self.assertIn('## 二\n\n本文', result.text)
        worker.assert_called_once()

    @patch('event.services.article_slide_images.run_pdf_worker', return_value=b'jpeg')
    def test_code_block_headings_are_ignored_and_reused_page_is_rendered_once(self, worker):
        result = self.generate('```markdown\n## 例\n```\n\n## 実践\n\n本文\n\n## 補足 ###\n', [
            {'heading': '例', 'page': 1}, {'heading': '実践', 'page': 2}, {'heading': '補足', 'page': 2},
        ])
        self.assertNotIn('![例のスライド]', result.text)
        self.assertEqual(result.text.count('!['), 2)
        worker.assert_called_once()

    @patch('event.services.article_slide_images.run_pdf_worker')
    def test_refused_article_does_not_publish_images(self, worker):
        self.detail.article_consent = 'ng'
        result = self.generate('## 導入\n\n本文', [{'heading': '導入', 'page': 1}])
        self.assertNotIn('![', result.text)
        worker.assert_not_called()

    @patch('event.services.article_slide_images.run_pdf_worker', return_value=b'jpeg')
    def test_private_pdf_is_rendered_into_public_storage_without_reading_private_url(self, worker):
        self.detail.slide_file = ''
        self.detail.slide_source_file = SimpleUploadedFile('source.pdf', b'private PDF fixture')
        self.sources = BlogSources('', '[ページ 1]\n導入', '', slide_name='source.pdf')
        with patch.object(self.detail.article_pdf.storage, 'url', side_effect=AssertionError('private URL read')):
            result = self.generate('## 導入\n\n本文', [{'heading': '導入', 'page': 1}])
        self.assertIn('](/media/slide_page/', result.text)

    @patch('event.services.article_slide_images.default_storage.save')
    def test_consent_withdrawn_while_rendering_does_not_publish_image(self, save):
        def render(*args, **kwargs):
            self.detail.article_consent = 'ng'
            return b'jpeg'

        with patch('event.services.article_slide_images.run_pdf_worker', side_effect=render):
            result = self.generate('## 導入\n\n本文', [{'heading': '導入', 'page': 1}])
        self.assertNotIn('![', result.text)
        save.assert_not_called()

    @patch('event.services.content_generation_service.run_pdf_worker', return_value=b'[page 1]')
    def test_collection_requests_page_markers_without_changing_old_extraction_default(self, worker):
        collect_blog_sources(self.detail, transcript='')
        self.assertTrue(worker.call_args.kwargs['with_page_numbers'])

    @patch('event.services.article_slide_images.run_pdf_worker', return_value=b'jpeg')
    @patch('event.services.article_slide_images.default_storage.save', side_effect=OSError('storage failed'))
    def test_storage_failure_does_not_lose_article(self, save, worker):
        result = self.generate('## 導入\n\n本文', [{'heading': '導入', 'page': 1}])
        self.assertEqual(result.text, '## 導入\n\n本文')

    @patch('event.services.article_slide_images.run_pdf_worker')
    def test_missing_optional_page_mapping_preserves_older_response(self, worker):
        result = self.generate('## 導入\n\n本文', [])
        self.assertEqual(result.text, '## 導入\n\n本文')
        worker.assert_not_called()


class ArticleImageSanitizationTests(SimpleTestCase):
    @override_settings(MEDIA_URL='https://media.example.test/files/', AWS_S3_CUSTOM_DOMAIN='cdn.example.test')
    def test_only_own_storage_and_existing_relative_images_survive(self):
        allowed = [f'https://data.{SITE_DOMAIN}/slide.jpg', f'https://{SITE_DOMAIN}/slide.jpg',
                   'https://media.example.test/slide.jpg', 'https://cdn.example.test/slide.jpg',
                   '/static/poster.png', 'images/example.jpg']
        denied = ['https://other.example/slide.jpg', 'https://cdn.example.test.evil.test/a.jpg',
                  f'https://{SITE_DOMAIN}.evil.test/a.jpg', '//other.example/a.jpg',
                  'data:image/png;base64,AA', 'javascript:alert(1)', '',
                  'https://user:password@cdn.example.test/a.jpg', '/\\evil.test/a.jpg']
        for src in allowed + denied:
            with self.subTest(src=src):
                for html in (convert_markdown(f'<img src="{src}" alt="図">'),
                             _sanitize_html(f'<img src="{src}" alt="図">')):
                    self.assertEqual('<img' in html, src in allowed)
        self.assertNotIn('<img', convert_markdown('![外部](https://other.example/a.jpg)'))

    @override_settings(STORAGES={'default': {'BACKEND': 'storages.backends.s3.S3Storage', 'OPTIONS': {
        'custom_domain': 'configured.example.test', 'endpoint_url': 'https://objects.example.test',
    }}})
    def test_storage_options_are_used_for_public_hosts(self):
        for host in ('configured.example.test', 'objects.example.test'):
            self.assertIn('<img', convert_markdown(f'![図](https://{host}/1.jpg)'))


class SlidePageWorkerTests(SimpleTestCase):
    def test_page_markers_preserve_original_numbers_and_budgets(self):
        reader = SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: '最初'),
                                       SimpleNamespace(extract_text=lambda: ''),
                                       SimpleNamespace(extract_text=lambda: '最後')])
        kwargs = {'reader_factory': lambda path: reader, 'max_chars': 100}
        self.assertEqual(event_pdf_worker.extract_pdf_text('unused', **kwargs), '最初\n最後')
        self.assertEqual(event_pdf_worker.extract_pdf_text('unused', with_page_numbers=True, **kwargs),
                         '[ページ 1]\n最初\n[ページ 3]\n最後')
        self.assertLessEqual(len(event_pdf_worker.extract_pdf_text(
            'unused', max_chars=12, with_page_numbers=True, reader_factory=lambda path: reader)), 12)

    @patch.object(event_pdf_worker, '_apply_resource_limits')
    @patch.object(event_pdf_worker, 'render_pdf_page')
    def test_cli_validates_page_argument_and_dispatches(self, render, limits):
        base = ['--operation', 'page_image', '--input', 'in.pdf', '--output', 'out.jpg']
        for args in ([], ['--page', '0'], ['--page', '-2']):
            self.assertEqual(event_pdf_worker.main(base + args), 2)
        render.assert_not_called()
        self.assertEqual(event_pdf_worker.main(base + ['--page', '2']), 0)
        render.assert_called_once_with('in.pdf', 'out.jpg', page=2)
        self.assertEqual(event_pdf_worker.main(base + ['--page', '2', '--with-page-numbers']), 2)
        self.assertEqual(event_pdf_worker.main([
            '--operation', 'thumbnail', '--input', 'in.pdf', '--output', 'out.jpg', '--page', '2',
        ]), 2)

    def test_parent_rejects_invalid_pages_before_spawning(self):
        with patch.object(pdf_worker, '_run_child') as child:
            for page in (None, 0, -1, True, '1', 1.5):
                with self.subTest(page=page), self.assertRaises(ValueError):
                    run_pdf_worker('page_image', 'unused', page=page)
            with self.assertRaises(ValueError):
                run_pdf_worker('thumbnail', 'unused', page=1)
            with self.assertRaises(ValueError):
                run_pdf_worker('thumbnail', 'unused', with_page_numbers=True)
            child.assert_not_called()

    def test_parent_passes_page_argument_through_isolated_worker(self):
        def child(argv):
            self.assertIn('-I', argv)
            self.assertEqual(argv[argv.index('--page') + 1], '2')
            Path(argv[argv.index('--output') + 1]).write_bytes(b'jpeg')
            return 0

        with tempfile.NamedTemporaryFile() as pdf, patch.object(pdf_worker, '_run_child', side_effect=child):
            self.assertEqual(run_pdf_worker('page_image', pdf.name, page=2), b'jpeg')

    def test_page_renderer_checks_actual_page_count_and_preserves_full_page(self):
        from PIL import Image
        image = Image.new('RGB', (800, 1200), 'white')
        document = MagicMock()
        document.__len__.return_value = 2
        page = document.__getitem__.return_value
        page.get_size.return_value = (800, 1200)
        page.render.return_value.to_pil.return_value = image
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            'sys.modules', {'pypdfium2': SimpleNamespace(PdfDocument=lambda path: document)},
        ):
            path = str(Path(directory) / 'page.jpg')
            with self.assertRaises(ValueError):
                event_pdf_worker.render_pdf_page('unused', path, page=3)
            document.__getitem__.assert_not_called()
            event_pdf_worker.render_pdf_page('unused', path, page=2)
            document.__getitem__.assert_called_once_with(1)
            with Image.open(path) as rendered:
                self.assertEqual(rendered.size, (800, 1200))
                self.assertEqual(rendered.format, 'JPEG')
            page.close.assert_called_once()
            page.render.return_value.close.assert_called_once()
            self.assertEqual(document.close.call_count, 2)
