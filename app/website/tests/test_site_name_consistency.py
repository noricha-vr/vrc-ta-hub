"""サイト名の公開表記と、request なしでのテンプレート描画を検証する。"""

import json
from html.parser import HTMLParser
from pathlib import Path

from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase

from guide.views import GUIDE_ROOT
from website.constants import SITE_NAME, SITE_NAME_SEO


class SiteNameConsistencyTests(SimpleTestCase):
    def test_site_name_tags_are_available_without_load(self):
        self.assertEqual(SITE_NAME, '技術・学術系イベントHub')
        self.assertEqual(SITE_NAME_SEO, 'VRChat 技術・学術系イベントHub')
        self.assertEqual(
            Template('{% site_name %}|{% site_name_seo %}').render(Context()),
            f'{SITE_NAME}|{SITE_NAME_SEO}',
        )

    def test_email_templates_render_site_name_without_request(self):
        templates = (
            'account/email/welcome.html',
            'account/email/admin_notification.html',
            'community/email/accept.html',
            'community/email/reject.html',
            'event/email/article_published.html',
            'event/email/lt_application_received.html',
            'event/email/lt_application_result.html',
            'event/email/material_upload_reminder.html',
            'event/email/slide_publication_reminder.html',
            'event/email/slide_publication_reminder.txt',
        )
        for template in templates:
            with self.subTest(template=template):
                rendered = render_to_string(template)
                self.assertIn(SITE_NAME, rendered)
                self.assertNotIn('{% site_name', rendered)
                self.assertNotIn(SITE_NAME_SEO, rendered)
                # タグの前に文字を直書きすると描画後に旧名が復活するので、描画結果でも確かめる
                self.assertNotIn(f'VRC{SITE_NAME}', rendered)
                self.assertNotIn(f'VRChat{SITE_NAME}', rendered)

    def test_manifest_uses_formal_name(self):
        app_root = Path(__file__).resolve().parents[2]
        manifest = json.loads((app_root / 'site/site.webmanifest').read_text(encoding='utf-8'))
        self.assertEqual(manifest['name'], SITE_NAME)
        self.assertEqual(manifest['short_name'], SITE_NAME)

    def test_old_site_names_are_absent_from_application_sources(self):
        app_root = Path(__file__).resolve().parents[2]
        old_names = (
            'VRC技術学術系Hub',
            'VRC技術学術Hub',
            'VRC技術学術ハブ',
            'VRC技術・学術系イベントHub',
            'VRChat技術・学術系イベントHub',
            'VRChat技術学術系イベントHub',
            '技術学術系Hub',
            'VRC技術学術集会',
            # タグの前に接頭辞を直書きすると描画後に旧名になる
            'VRC{% site_name',
            'VRChat{% site_name',
        )
        violations = []
        # 使い方ガイドは docs/guide/ の Markdown をそのまま画面に出すので、app/ と同じく検査する
        self.assertTrue(GUIDE_ROOT.is_dir(), f'ガイドのディレクトリが見つからない: {GUIDE_ROOT}')
        paths = [(app_root, path) for path in sorted(app_root.rglob('*'))]
        paths += [(GUIDE_ROOT, path) for path in sorted(GUIDE_ROOT.rglob('*.md'))]
        for root, path in paths:
            relative = path.relative_to(root)
            if (
                not path.is_file()
                or path.suffix not in {'.html', '.txt', '.md', '.py', '.webmanifest'}
                # test-emails はローカルのメール出力先（git 管理外）
                or {'fixtures', 'migrations', 'tests', 'test-emails'} & set(relative.parts[:-1])
                or path.match('test_*.py')
            ):
                continue
            for line_number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                for name in old_names:
                    if name in line:
                        violations.append(f'{root.name}/{relative}:{line_number}: {name}')
        self.assertEqual(violations, [], '\n'.join(violations))


class _MetaParser(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.site_names = []
        self.feed(content.decode('utf-8'))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and attrs.get('property') == 'og:site_name':
            self.site_names.append(attrs.get('content'))


class SiteNameMetadataTests(TestCase):
    def test_top_and_child_pages_have_one_formal_og_site_name(self):
        # meta ブロックを上書きする子テンプレートにも共通のタグが残ること。
        for path in ('/', '/news/', '/guide/', '/account/login/'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(_MetaParser(response.content).site_names, [SITE_NAME])
