"""ガイド本文・目次・画像を実ファイルと照合する契約テスト。"""
import hashlib
import posixpath
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

import markdown
import yaml
from bs4 import BeautifulSoup
from django.http import Http404
from django.templatetags.static import static
from django.test import SimpleTestCase, TestCase, override_settings

from event.services.markdown_processor import convert_markdown
from guide.rendering import is_public_path, page_table_of_contents
from guide.views import GUIDE_REDIRECTS, GUIDE_ROOT, load_markdown_content

IMAGE_ROOT = Path(__file__).resolve().parents[1] / 'static' / 'guide' / 'images'


def public_pages():
    return {file.relative_to(GUIDE_ROOT).with_suffix('').as_posix(): file
            for file in GUIDE_ROOT.rglob('*.md')
            if is_public_path(file.relative_to(GUIDE_ROOT).as_posix())}


def nav_paths(items):
    for item in items:
        if 'path' in item:
            yield item['path']
        yield from nav_paths(item.get('children', []))


def versioned(path):
    digest = hashlib.md5((IMAGE_ROOT.parent / path).read_bytes(), usedforsecurity=False).hexdigest()[:10]
    return f"{static(f'guide/{path}')}?v={digest}"


class GuideContentContractTest(SimpleTestCase):
    def assert_link_anchors_exist(self, rendered_pages):
        for page, soup in rendered_pages.items():
            for link in soup.select('a[href]'):
                href = str(link['href'])
                url = urlsplit(href)
                if url.scheme or url.netloc or not url.fragment:
                    continue
                if not url.path:
                    target = page
                elif url.path.startswith('/guide/'):
                    target = unquote(url.path[len('/guide/'):]).strip('/') or 'index'
                    target = GUIDE_REDIRECTS.get(target, target)
                else:
                    continue
                with self.subTest(page=page, href=href, target=target):
                    self.assertIn(target, rendered_pages, 'リンク先ページがありません')
                    self.assertIsNotNone(rendered_pages[target].find(id=unquote(url.fragment)),
                                         f'リンク先の id がありません: {page} → {href}')

    def test_internal_link_anchors_exist_in_rendered_pages(self):
        rendered_pages = {
            page: BeautifulSoup(load_markdown_content(page)[0], 'html.parser')
            for page in public_pages()
        }
        self.assert_link_anchors_exist(rendered_pages)

    def test_anchor_contract_checks_same_and_other_pages(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'section').mkdir()
            (root / 'section' / 'page.md').write_text(
                '## 1. 案内を開く\n\n[同じページ](#1-案内を開く)\n\n'
                '[別ページ](../other.md?from=guide#うまくいかない時)')
            (root / 'other.md').write_text('## うまくいかない時')
            with patch('guide.views.GUIDE_ROOT', root):
                pages = {page: BeautifulSoup(load_markdown_content(page)[0], 'html.parser')
                         for page in ('section/page', 'other')}
        self.assert_link_anchors_exist(pages)
        pages['other'].h2['id'] = '別の見出し'
        with self.assertRaisesRegex(AssertionError, 'リンク先の id がありません'):
            # subTest の失敗を呼び出し元へ伝えるため、通常の TestCase と切り離して検証する。
            GuideContentContractTest().assert_link_anchors_exist(pages)

    def test_navigation_matches_public_files(self):
        items = yaml.safe_load((GUIDE_ROOT / '_nav.yaml').read_text())
        paths = list(nav_paths(items))
        self.assertEqual(len(paths), len(set(paths)), '目次に重複があります')
        self.assertIn('index', paths)
        self.assertSetEqual(set(paths), set(public_pages()))

    def test_internal_links_exist(self):
        pages = public_pages()
        for page, file in pages.items():
            soup = BeautifulSoup(markdown.markdown(file.read_text(), extensions=['fenced_code']), 'html.parser')
            for link in soup.select('a[href]'):
                href = str(link['href'])
                url = urlsplit(href)
                if url.scheme or url.netloc or not url.path:
                    continue
                path = unquote(url.path)
                if path.startswith('/guide/'):
                    target = path[len('/guide/'):].strip('/') or 'index'
                elif path.endswith('.md'):
                    target = posixpath.normpath(posixpath.join(posixpath.dirname(page), path))[:-3]
                else:
                    continue
                with self.subTest(page=page, href=href):
                    self.assertIn(target, pages)

    def test_images_exist_and_have_alt(self):
        for page, file in public_pages().items():
            soup = BeautifulSoup(markdown.markdown(file.read_text(), extensions=['fenced_code']), 'html.parser')
            for img in soup.find_all('img'):
                with self.subTest(page=page, src=img.get('src')):
                    self.assertTrue(str(img.get('alt', '')).strip(), '画像の alt が空です')
                    src = str(img.get('src', ''))
                    if src.startswith('images/'):
                        image = (IMAGE_ROOT / unquote(urlsplit(src).path)[len('images/'):]).resolve()
                        self.assertTrue(image.is_relative_to(IMAGE_ROOT.resolve()))
                        self.assertTrue(image.is_file(), f'画像がありません: {src}')

    def test_screenshots_are_all_referenced_from_pages(self):
        """撮り直しで番号が減った時などの置き忘れを防ぐ。-sp 版は本文に書かず、PC 版と対で置く。"""
        referenced = set()
        for page, file in public_pages().items():
            soup = BeautifulSoup(markdown.markdown(file.read_text(), extensions=['fenced_code']), 'html.parser')
            for img in soup.find_all('img'):
                src = unquote(urlsplit(str(img.get('src', ''))).path)
                if src.startswith('images/'):
                    with self.subTest(page=page, src=src):
                        self.assertFalse(src.endswith('-sp.webp'), '-sp 版は本文に書きません（スマホ幅で自動で差し替わります）')
                    referenced.add(src[len('images/'):])
        for image in IMAGE_ROOT.rglob('*.webp'):
            name = image.relative_to(IMAGE_ROOT).as_posix()
            pc_name = name.removesuffix('-sp.webp') + '.webp' if name.endswith('-sp.webp') else name
            with self.subTest(image=name):
                self.assertIn(pc_name, referenced, 'どのページからも使われていない画像です')


class GuideRenderingTest(SimpleTestCase):
    def render(self, body):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'section').mkdir()
            (root / 'section' / 'page.md').write_text(body)
            with patch('guide.views.GUIDE_ROOT', root):
                html, _ = load_markdown_content('section/page')
        return BeautifulSoup(html, 'html.parser')

    def test_heading_ids_follow_unicode_slug_rules_after_sanitization(self):
        soup = self.render('##  1. 連携の案内を開く  \n\n'
                           '### うまくいかない時\n\n'
                           '####  A_B   C-D！ <em>確認</em>')
        self.assertEqual([h['id'] for h in soup.find_all(['h2', 'h3', 'h4'])],
                         ['1-連携の案内を開く', 'うまくいかない時', 'A_B-C-D-確認'])

    def test_heading_ids_are_unique_across_levels_and_numbered_slugs(self):
        soup = self.render('## 確認\n\n### 確認\n\n#### 確認\n\n## 確認-2\n\n### 確認')
        self.assertEqual([h['id'] for h in soup.find_all(['h2', 'h3', 'h4'])],
                         ['確認', '確認-2', '確認-3', '確認-2-2', '確認-4'])

    def test_tables_get_scroll_wrappers_without_changing_cells(self):
        soup = self.render('| 項目 | 必須か |\n| --- | --- |\n| 名前 | 必須 |\n\n'
                           '<table><tr><td>別の表</td></tr></table>')
        tables = soup.find_all('table')
        self.assertEqual(len(tables), 2)
        for table in tables:
            self.assertEqual(table.parent.name, 'div')
            self.assertEqual(table.parent['class'], ['guide-table-wrap'])
        self.assertEqual([cell.get_text() for cell in tables[0].select('th, td')],
                         ['項目', '必須か', '名前', '必須'])

    def test_page_toc_nests_h3_under_h2_and_excludes_h4(self):
        soup = self.render('## 始める前に\n\n### 用意するもの\n\n#### 補足\n\n'
                           '## 手順\n\n### 1. 開く\n\n### 2. 入力する')
        toc = page_table_of_contents(str(soup))
        self.assertEqual([item['title'] for item in toc], ['始める前に', '手順'])
        self.assertEqual([child['id'] for child in toc[0]['children']], ['用意するもの'])
        self.assertEqual([child['id'] for child in toc[1]['children']], ['1-開く', '2-入力する'])

    def test_page_toc_is_hidden_with_fewer_than_two_h2_or_h3(self):
        for body in ('本文だけ', '## ひとつ\n\n#### 補足', '### ひとつ'):
            with self.subTest(body=body):
                self.assertEqual(page_table_of_contents(str(self.render(body))), [])

    def test_page_toc_preserves_h3_before_first_h2(self):
        toc = page_table_of_contents(str(self.render('### 案内\n\n## 手順\n\n### 操作')))
        self.assertEqual([item['title'] for item in toc], ['案内', '手順'])
        self.assertEqual(toc[1]['children'][0]['title'], '操作')

    @override_settings(STATIC_URL='https://assets.example.com/')
    def test_relative_images_use_static_storage_after_sanitization(self):
        soup = self.render('![赤枠の入力欄](images/section/page-01.webp)')
        img = soup.img
        self.assertEqual(img['src'], static('guide/images/section/page-01.webp'))
        self.assertEqual(img['loading'], 'lazy')
        self.assertIn('guide-screenshot', img['class'])
        self.assertEqual(img.parent['href'], img['src'])
        self.assertEqual(img.parent['target'], '_blank')
        self.assertEqual(img.parent['rel'], ['noopener'])
        self.assertEqual(img.parent['class'], ['guide-screenshot-link'])

    def test_mobile_variant_is_used_on_small_screens(self):
        # community/create-01 には撮影済みの -sp 版がある。settings/discord-02 はモックで -sp 版を撮らない前提ではなく、
        # 実在するかどうかでだけ切り替わることを確かめる。
        soup = self.render('![入口](images/community/create-01.webp)')
        picture = soup.img.parent
        self.assertEqual(picture.name, 'picture')
        self.assertEqual(picture.source['srcset'], versioned('images/community/create-01-sp.webp'))
        self.assertEqual(picture.source['media'], '(max-width: 575.98px)')
        self.assertEqual(picture.parent.name, 'a')
        self.assertEqual(picture.parent['href'], versioned('images/community/create-01.webp'))

    def test_existing_image_url_changes_with_its_content(self):
        # 配信側は同じ URL の画像を数時間キャッシュする。撮り直した画像が古いまま出ないよう、中身のハッシュを URL に付ける。
        soup = self.render('![入口](images/community/create-01.webp)')
        self.assertTrue(soup.img['src'].startswith(static('guide/images/community/create-01.webp') + '?v='))
        self.assertEqual(soup.img['src'], versioned('images/community/create-01.webp'))

    def test_image_without_mobile_variant_is_not_wrapped_in_picture(self):
        soup = self.render('![入力欄](images/section/page-01.webp)')
        self.assertIsNone(soup.find('picture'))

    def test_raw_html_image_gets_display_attributes(self):
        soup = self.render('<img src="images/section/page-01.webp" alt="① の入力欄" class="existing">')
        self.assertEqual(soup.img['src'], static('guide/images/section/page-01.webp'))
        self.assertEqual(soup.img['loading'], 'lazy')
        self.assertEqual(soup.img['class'], ['existing', 'guide-screenshot'])
        self.assertEqual(soup.img.parent.name, 'a')

    def test_existing_anchor_is_not_nested(self):
        soup = self.render('[![入力欄](images/section/page-01.webp)](../index.md)')
        self.assertEqual(len(soup.find_all('a')), 1)
        self.assertEqual(soup.img.parent['href'], '/guide/index/')
        self.assertNotIn('guide-screenshot-link', soup.img.parent.get('class', []))

    def test_external_and_absolute_images_are_unchanged(self):
        for src in ('https://example.com/image.webp', 'http://example.com/image.webp', '/images/a.webp', '//example.com/a.webp'):
            with self.subTest(src=src):
                img = self.render(f'<img src="{src}" alt="参考図">').img
                self.assertEqual(img['src'], src)
                self.assertNotIn('loading', img.attrs)

    def test_relative_markdown_links_keep_query_and_fragment(self):
        self.assertIn('href="../index.md"', convert_markdown('[はじめに](../index.md)'))
        soup = self.render('[はじめに](../index.md?from=guide#first)\n\n[同階層](other.md)\n\n[外部](https://example.com/a.md)')
        self.assertEqual([a['href'] for a in soup.find_all('a')],
                         ['/guide/index/?from=guide#first', '/guide/section/other/', 'https://example.com/a.md'])


class GuidePrivateFilesTest(TestCase):
    def test_private_pages_and_og_return_404(self):
        # 実在する資料を作る。単に「ファイルが無いから404」のテストにしない。
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = ['AGENTS', '_coverage', '_review', '_nav', '_private/page', 'section/_draft', 'section/AGENTS', 'agents', 'Agents']
            for path in paths:
                file = root / f'{path}.md'
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text('---\ntitle: 内部資料\n---\n非公開の本文')
            with patch('guide.views.GUIDE_ROOT', root):
                for path in paths:
                    with self.subTest(path=path):
                        for render_body in (True, False):
                            with self.assertRaises(Http404):
                                load_markdown_content(path, render_body=render_body)
                        self.assertEqual(self.client.get(f'/guide/{path}/').status_code, 404)
                        self.assertEqual(self.client.get(f'/og/guide/{path}.png').status_code, 404)
