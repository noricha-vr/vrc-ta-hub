"""ステージ登録後のリンクは設定を優先し、既知の登録 URL だけから補完する。"""
from types import SimpleNamespace

from django.test import SimpleTestCase

from vket.views.status import _resolve_stage_edit_url, _resolve_stage_list_url


class StageRegistrationURLTests(SimpleTestCase):
    def resolve(self, settings):
        collaboration = SimpleNamespace(settings_json=settings, slug='2026-winter')
        return _resolve_stage_edit_url(collaboration), _resolve_stage_list_url(collaboration)

    def test_known_registration_url_derives_both_links_without_queries(self):
        # SimpleTestCase は DB アクセスを許可しない。
        for event in ('2026Winter', '2027Summer', 'Event123'):
            for suffix in ('', '/'):
                with self.subTest(event=event, suffix=suffix):
                    self.assertEqual(self.resolve({
                        'stage_url': f'https://vket.com/mypage/event/{event}/my_notification/new{suffix}',
                    }), (
                        f'https://vket.com/mypage/event/{event}/my_notification',
                        f'https://vket.com/hub/{event}/notification',
                    ))

    def test_explicit_https_settings_override_derived_links_independently(self):
        stage_url = 'https://vket.com/mypage/event/2026Winter/my_notification/new'
        self.assertEqual(self.resolve({
            'stage_url': stage_url, 'stage_edit_url': ' https://example.com/edit ',
            'stage_list_url': ' https://example.com/list ',
        }), ('https://example.com/edit', 'https://example.com/list'))
        self.assertEqual(self.resolve({
            'stage_url': stage_url, 'stage_edit_url': 'https://example.com/edit',
            'stage_list_url': ' ',
        }), ('https://example.com/edit', 'https://vket.com/hub/2026Winter/notification'))
        self.assertEqual(self.resolve({
            'stage_edit_url': 'https://example.com/edit', 'stage_list_url': 'https://example.com/list',
        }), ('https://example.com/edit', 'https://example.com/list'))

    def test_unexpected_registration_urls_do_not_produce_links(self):
        for url in (
            'https://example.com/mypage/event/2026Winter/my_notification/new',
            'http://vket.com/mypage/event/2026Winter/my_notification/new',
            'https://vket.com/mypage/event/2026-Winter/my_notification/new',
            'https://vket.com/mypage/event/2026_Winter/my_notification/new',
            'https://vket.com/mypage/event/冬/my_notification/new',
            'https://vket.com/mypage/event/2026Winter/my_notification/new?extra=1',
            'https://vket.com/mypage/event/2026Winter/my_notification/new#extra',
            'https://vket.com/mypage/event/2026Winter/my_notification/new/extra',
            'https://vket.com/hub/2026Summer/notification',
            'javascript:alert(1)', '',
        ):
            with self.subTest(url=url):
                self.assertEqual(self.resolve({'stage_url': url}), ('', ''))
        for settings in (None, [], {}, {'stage_url': 123}):
            with self.subTest(settings=settings):
                self.assertEqual(self.resolve(settings), ('', ''))

    def test_non_https_explicit_settings_are_suppressed_without_fallback(self):
        for value in ('javascript:alert(1)', 'data:text/html,test', '//example.com/path',
                      'http://example.com/path', '/relative/path', 'https://', 'https://vket.com@evil.invalid/',
                      'https://vket.com/a\nb', 'https://vket.com/a b', 'https://[example.com/edit'):
            with self.subTest(value=value):
                self.assertEqual(self.resolve({
                    'stage_url': 'https://vket.com/mypage/event/2026Winter/my_notification/new',
                    'stage_edit_url': value, 'stage_list_url': value,
                }), ('', ''))
