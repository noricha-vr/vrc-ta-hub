"""ガイド本文・目次・画像を実ファイルと照合する契約テスト。"""
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
from guide.rendering import is_public_path
from guide.views import GUIDE_ROOT, load_markdown_content

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


class GuideContentContractTest(SimpleTestCase):
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

    def test_mobile_variant_is_used_on_small_screens(self):
        # community/create-01 には撮影済みの -sp 版がある。settings/discord-02 はモックで -sp 版を撮らない前提ではなく、
        # 実在するかどうかでだけ切り替わることを確かめる。
        soup = self.render('![入口](images/community/create-01.webp)')
        picture = soup.img.parent
        self.assertEqual(picture.name, 'picture')
        self.assertEqual(picture.source['srcset'], static('guide/images/community/create-01-sp.webp'))
        self.assertEqual(picture.source['media'], '(max-width: 575.98px)')
        self.assertEqual(picture.parent.name, 'a')
        self.assertEqual(picture.parent['href'], static('guide/images/community/create-01.webp'))

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
