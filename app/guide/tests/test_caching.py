"""ガイドのキャッシュ再利用・更新検知のテスト。"""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml
from django.http import Http404
from django.test import TestCase

from event.services.markdown_processor import convert_markdown
from guide.rendering import page_table_of_contents
from guide.views import (
    _load_markdown_content_cached,
    _load_navigation_cached,
    load_markdown_content,
    load_navigation,
)


class GuideCachingTest(TestCase):
    def setUp(self):
        for cache in (_load_markdown_content_cached, _load_navigation_cached, page_table_of_contents):
            cache.cache_clear()
            self.addCleanup(cache.cache_clear)
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        patcher = patch('guide.views.GUIDE_ROOT', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.page = self.root / 'page.md'
        self.page.write_text('---\ntitle: 案内\noptions:\n  labels: [元の値]\n---\n最初の本文', encoding='utf-8')
        self.nav = self.root / '_nav.yaml'
        self.nav.write_text('- title: 案内\n  path: page\n', encoding='utf-8')

    def rewrite(self, file, content, *, preserve_mtime=False):
        stat = file.stat()
        file.write_text(content, encoding='utf-8')
        mtime = stat.st_mtime_ns if preserve_mtime else stat.st_mtime_ns + 1_000_000
        os.utime(file, ns=(stat.st_atime_ns, mtime))

    def test_repeated_page_requests_convert_markdown_once(self):
        with patch('guide.views.convert_markdown', wraps=convert_markdown) as convert:
            for _ in range(2):
                response = self.client.get('/guide/page/')
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, '最初の本文')
            convert.assert_called_once()

    def test_debug_renders_without_cache(self):
        with self.settings(DEBUG=True), patch('guide.views.convert_markdown', wraps=convert_markdown) as convert:
            load_markdown_content('page')
            load_markdown_content('page')
            self.assertEqual(convert.call_count, 2)

    def test_markdown_mtime_invalidates_cache_without_size_change(self):
        original, _ = load_markdown_content('page')
        stat = self.page.stat()
        self.rewrite(self.page, self.page.read_text(encoding='utf-8').replace('最初', '更新'))
        self.assertEqual(self.page.stat().st_size, stat.st_size)
        updated, _ = load_markdown_content('page')
        self.assertNotEqual(original, updated)
        self.assertIn('更新の本文', updated)

    def test_markdown_size_invalidates_cache_without_mtime_change(self):
        load_markdown_content('page')
        self.rewrite(self.page, '更新された長さの違う本文', preserve_mtime=True)
        self.assertIn('更新された長さの違う本文', load_markdown_content('page')[0])

    def test_frontmatter_copy_includes_nested_values(self):
        _, frontmatter = load_markdown_content('page')
        frontmatter['title'] = '書き換え'
        frontmatter['options']['labels'].append('追加')
        _, original = load_markdown_content('page')
        self.assertEqual(original, {'title': '案内', 'options': {'labels': ['元の値']}})

    def test_metadata_only_cache_is_separate_from_rendered_body(self):
        with patch('guide.views.convert_markdown', wraps=convert_markdown) as convert:
            for _ in range(2):
                body, frontmatter = load_markdown_content('page', render_body=False)
                self.assertEqual(body, '')
                self.assertEqual(frontmatter['title'], '案内')
            convert.assert_not_called()
            self.assertIn('最初の本文', load_markdown_content('page')[0])
            convert.assert_called_once()

    def test_cached_page_still_checks_public_path(self):
        load_markdown_content('page')
        with patch('guide.views.is_public_path', return_value=False):
            with self.assertRaises(Http404):
                load_markdown_content('page')
        self.page.unlink()
        with self.assertRaises(Http404):
            load_markdown_content('page')

    def test_navigation_is_parsed_once_and_returns_independent_items(self):
        with patch('guide.views.yaml.safe_load', wraps=yaml.safe_load) as parse:
            items = load_navigation()
            items[0].title = '書き換え'
            self.assertEqual(load_navigation()[0].title, '案内')
            parse.assert_called_once()

    def test_navigation_mtime_invalidates_cache_without_size_change(self):
        self.assertEqual(load_navigation()[0].title, '案内')
        stat = self.nav.stat()
        self.rewrite(self.nav, '- title: 更新\n  path: page\n')
        self.assertEqual(self.nav.stat().st_size, stat.st_size)
        self.assertEqual(load_navigation()[0].title, '更新')

    def test_navigation_size_invalidates_cache_without_mtime_change(self):
        load_navigation()
        self.rewrite(self.nav, '- title: 長さの違う案内\n  path: page\n', preserve_mtime=True)
        self.assertEqual(load_navigation()[0].title, '長さの違う案内')

    def test_missing_and_invalid_navigation_recovers_after_update(self):
        self.nav.unlink()
        self.assertEqual(load_navigation(), [])
        self.nav.write_text('[', encoding='utf-8')
        self.assertEqual(load_navigation(), [])
        self.rewrite(self.nav, '- title: 復旧\n  path: page\n')
        self.assertEqual(load_navigation()[0].title, '復旧')

    def test_table_of_contents_is_parsed_once_for_identical_html(self):
        from bs4 import BeautifulSoup

        html = '<h2 id="案内">案内</h2><h3 id="手順">手順</h3>'
        with patch('guide.rendering.BeautifulSoup', wraps=BeautifulSoup) as parse:
            first = page_table_of_contents(html)
            self.assertEqual(first, page_table_of_contents(html))
            parse.assert_called_once()

