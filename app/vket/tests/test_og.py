"""匿名プレビュー・署名・サムネイル描画の回帰テスト。"""
from datetime import timedelta
from hashlib import sha256
from html import escape
from io import BytesIO
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from django.conf import settings
from django.db import connection
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from PIL import Image, ImageFont

from community.models import Community, CommunityMember
from tests.factories import make_community, make_user
from vket.models import VketCollaboration, VketNotice, VketNoticeReceipt, VketParticipation
from vket.og import (
    ASSET_ROOT, notice_base, notice_image_url,
    notice_link, render_notice_png, sign_notice, valid_notice_signature,
)
from ta_hub.og_image import (
    ASSET_ROOT as SHARED_ASSET_ROOT, NO_LINE_START, _wrap_text,
    _render_text_png_cached as _render_notice_png_cached,
)
from vket.views.notice import _build_remind_text
from website.constants import DEFAULT_NEWS_IMAGE_URL

OG_IMAGES = {
    'apply': 'vket/og/vket-2026-winter/apply.jpg',
    'status': 'vket/og/vket-2026-winter/status.jpg',
    'stage_register': 'vket/og/vket-2026-winter/stage-register.jpg',
    'detail': 'vket/og/vket-2026-winter/detail.jpg',
    'notice_base': 'vket-2026-winter',
}


class VketOgTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.owner = make_user(user_name='og_owner', email='og_owner@example.com')
        cls.community = make_community(name='非公開の集会', owner=cls.owner)
        cls.collaboration = VketCollaboration.objects.create(
            name='Vket テスト', slug='og-test', phase=VketCollaboration.Phase.ENTRY_OPEN,
            period_start=today, period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1), lt_deadline=today + timedelta(days=3),
            settings_json={'og_images': OG_IMAGES},
        )
        cls.notice = VketNotice.objects.create(
            collaboration=cls.collaboration, title='参加登録のご案内', body='非公開の本文', sent_at=timezone.now(),
        )
        cls.participation = VketParticipation.objects.create(collaboration=cls.collaboration, community=cls.community)
        cls.receipt = VketNoticeReceipt.objects.create(notice=cls.notice, participation=cls.participation)

    def url(self, page):
        return reverse(f'vket:{page}', kwargs={'pk': self.collaboration.pk})

    def set_settings(self, value):
        self.collaboration.settings_json = value
        self.collaboration.save(update_fields=['settings_json'])

    def image_url(self, notice=None):
        kwargs = {'pk': self.collaboration.pk}
        if notice is not None:
            kwargs['notice_id'] = notice.pk
        return reverse('vket:notice_og_detail' if notice else 'vket:notice_og_list', kwargs=kwargs)

    def assert_png(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertEqual(response['Cache-Control'], 'public, max-age=86400')
        with Image.open(BytesIO(response.content)) as image:
            self.assertEqual(image.format, 'PNG')
            self.assertEqual(image.size, (1200, 630))
            image.load()

    def assert_private_shell(self, response):
        self.assertContains(response, '<meta name="robots" content="noindex,follow">', html=True)
        self.assertContains(response, '<meta name="twitter:card" content="summary_large_image">', html=True)
        self.assertIn('private', response['Cache-Control'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('Cookie', response['Vary'])
        self.assertEqual(response['X-Robots-Tag'], 'noindex')
        self.assertNotContains(response, self.notice.body)
        self.assertNotContains(response, self.community.name)

    def test_all_non_draft_shells_have_og_without_private_queries(self):
        for phase in VketCollaboration.Phase.values:
            if phase == VketCollaboration.Phase.DRAFT:
                continue
            self.collaboration.phase = phase
            self.collaboration.save(update_fields=['phase'])
            for page, label in [('apply', '参加登録'), ('status', '参加状況'), ('notice_list', 'お知らせ')]:
                if phase == VketCollaboration.Phase.ARCHIVED and page != 'notice_list':
                    continue  # 参加ページはアーカイブ済みを案内しない（test_archived_collaboration_keeps_login_redirect）
                with self.subTest(page=page, phase=phase):
                    with CaptureQueriesContext(connection) as queries:
                        response = self.client.get(self.url(page))
                    self.assert_private_shell(response)
                    self.assertContains(response, f'<meta property="og:title" content="Vket テスト {label}">', html=True)
                    sql = ' '.join(q['sql'].lower() for q in queries)
                    for model in (VketNotice, VketParticipation, VketNoticeReceipt, Community, CommunityMember):
                        self.assertNotIn(model._meta.db_table.lower(), sql)
                    if page in ('apply', 'status'):
                        self.assertContains(response, f'http://testserver/static/{OG_IMAGES[page]}', count=2)
                    else:
                        self.assertIn('/notices/og.png?v=', response.context['og_image_url'])

    def test_draft_and_missing_shells_preserve_login_redirect(self):
        self.collaboration.phase = VketCollaboration.Phase.DRAFT
        self.collaboration.save(update_fields=['phase'])
        for pk in (self.collaboration.pk, 999999):
            for page in ('apply', 'status', 'notice_list'):
                response = self.client.get(reverse(f'vket:{page}', kwargs={'pk': pk}))
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response['Location'].startswith(settings.LOGIN_URL))
                self.assertNotContains(response, self.collaboration.name, status_code=302)

    def test_archived_collaboration_keeps_login_redirect(self):
        # ログイン後は ARCHIVED が 404 になるので、未ログインでも案内ページを出さない。
        self.collaboration.phase = VketCollaboration.Phase.ARCHIVED
        self.collaboration.save(update_fields=['phase'])
        for page in ('apply', 'status', 'stage_register'):
            response = self.client.get(self.url(page))
            self.assertEqual(response.status_code, 302, page)
            self.assertIn(settings.LOGIN_URL, response['Location'])

    def test_participation_anonymous_post_and_head_remain_redirects(self):
        for page in ('apply', 'status'):
            for method in (self.client.post, self.client.head):
                self.assertEqual(method(self.url(page)).status_code, 302)

    def test_login_next_preserves_and_escapes_query(self):
        for page in ('apply', 'status'):
            response = self.client.get(self.url(page), {'q': '"><script>x</script>'})
            self.assertEqual(parse_qs(urlsplit(response.context['login_url']).query)['next'], [response.wsgi_request.get_full_path()])
            self.assertNotContains(response, '<script>x</script>')

    def test_authenticated_participation_og(self):
        self.client.force_login(self.owner)
        for page, label in [('apply', '参加登録'), ('status', '参加状況')]:
            response = self.client.get(self.url(page))
            self.assertContains(response, f'http://testserver/static/{OG_IMAGES[page]}', count=2)
            self.assertContains(response, f'<meta property="og:title" content="Vket テスト {label}">', html=True)

    def test_cdn_url_is_not_rebuilt(self):
        for page in ('apply', 'status'):
            with patch('ta_hub.og_image.static', return_value='https://cdn.example.com/og.jpg') as mock_static:
                response = self.client.get(self.url(page))
            mock_static.assert_called_once_with(OG_IMAGES[page])
            self.assertContains(response, 'https://cdn.example.com/og.jpg', count=2)
            self.assertNotContains(response, 'testserver/https:')

    def test_missing_and_invalid_settings_fall_back(self):
        for value in (None, [], {}, {'og_images': []}, {'og_images': 'bad'}):
            self.set_settings(value)
            for page in ('apply', 'status', 'notice_list'):
                self.assertContains(self.client.get(self.url(page)), DEFAULT_NEWS_IMAGE_URL, count=2)
        for bad in (None, 3, [], '', '../secret', '/etc/passwd', '//[', 'https://example.com/x', 'a\\b'):
            self.set_settings({'og_images': {'apply': bad, 'status': bad, 'notice_base': bad}})
            for page in ('apply', 'status', 'notice_list'):
                self.assertContains(self.client.get(self.url(page)), DEFAULT_NEWS_IMAGE_URL, count=2)

    def test_invalid_bases_redirect_and_preserve_legacy(self):
        for bad in (None, 4, [], '', '..', '../fonts', 'a/b', 'a\\b', 'does-not-exist'):
            self.set_settings({'og_images': {'notice_base': bad}, 'notice_og_image': 'vket/old.png'})
            self.assertContains(self.client.get(self.url('notice_list')), 'http://testserver/static/vket/old.png', count=2)
            self.assertRedirects(self.client.get(self.image_url()), DEFAULT_NEWS_IMAGE_URL, fetch_redirect_response=False)

    def test_list_png_and_hash_version(self):
        self.assert_png(self.client.get(self.image_url(), {'v': 'ignored'}))
        response = self.client.get(self.url('notice_list'))
        title = f'{self.collaboration.name}のお知らせ'
        self.assertIn('v=' + sha256(title.encode()).hexdigest()[:8], response.context['og_image_url'])

    def test_png_render_is_reused_for_same_title(self):
        # 署名なしで叩ける一覧画像を連打されても、同じタイトルは描き直さない。
        _render_notice_png_cached.cache_clear()
        with patch('ta_hub.og_image.Image.open', wraps=Image.open) as opened:
            responses = [self.client.get(self.image_url(), {'v': v}) for v in ('a', 'b', 'c')]
        self.assertEqual(opened.call_count, 1)
        for response in responses:
            self.assert_png(response)

    def test_signed_link_without_notice_base_keeps_legacy_image(self):
        self.set_settings({'notice_og_image': 'vket/old.png'})
        response = self.client.get(self.url('notice_list'), {'open': self.notice.pk, 's': sign_notice(self.notice.pk)})
        self.assertEqual(response.context['og_image_url'], 'http://testserver/static/vket/old.png')
        self.assertContains(response, self.notice.title)

    def test_og_url_keeps_signed_query_only_when_valid(self):
        signature = sign_notice(self.notice.pk)
        response = self.client.get(self.url('notice_list'), {'open': self.notice.pk, 's': signature})
        self.assertContains(response, f'<meta property="og:url" content="https://vrc-ta-hub.com{escape(response.context["og_url_path"])}">')
        self.assertIn(f'open={self.notice.pk}', response.context['og_url_path'])
        for query in ({}, {'open': self.notice.pk, 's': 'bad'}):
            response = self.client.get(self.url('notice_list'), query)
            self.assertEqual(response.context['og_url_path'], self.url('notice_list'))
        response = self.client.get(self.url('apply'), {'x': '1'})
        self.assertEqual(response.context['og_url_path'], self.url('apply'))

    def test_signed_shell_and_png_only_expose_title(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url('notice_list'), {'open': self.notice.pk, 's': sign_notice(self.notice.pk)})
        self.assert_private_shell(response)
        self.assertEqual(response.context['og_title'], self.notice.title)
        sql = ' '.join(q['sql'] for q in queries)
        self.assertNotIn('"body"', sql)
        for model in (VketParticipation, VketNoticeReceipt, CommunityMember):
            self.assertNotIn(model._meta.db_table, sql)
        self.assert_png(self.client.get(response.context['og_image_url']))
        image = urlsplit(response.context['og_image_url'])
        self.assertTrue(image.path.endswith(f'/{self.notice.pk}/og.png'))
        self.assertTrue(valid_notice_signature(self.notice.pk, parse_qs(image.query)['s'][0]))

    def test_unsigned_or_invalid_shell_never_queries_notice(self):
        for query in ({'open': self.notice.pk}, {'open': self.notice.pk, 's': 'bad'},
                      {'open': '9' * 100, 's': 'bad'}, {'open': 'bad', 's': sign_notice(self.notice.pk)}):
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(self.url('notice_list'), query)
            self.assertNotContains(response, self.notice.title)
            self.assertNotIn(VketNotice._meta.db_table, ' '.join(q['sql'] for q in queries))
            self.assertIn('/notices/og.png?', response.context['og_image_url'])

    def test_png_missing_invalid_and_mismatched_signature_is_404(self):
        for query in ({}, {'s': 'bad'}, {'s': sign_notice(self.notice.pk + 1)}, {'s': 'x' * 500}):
            response = self.client.get(self.image_url(self.notice), query)
            self.assertEqual(response.status_code, 404)
            self.assertNotContains(response, self.notice.title, status_code=404)
        missing = reverse('vket:notice_og_detail', kwargs={'pk': self.collaboration.pk, 'notice_id': 99999})
        self.assertEqual(self.client.get(missing, {'s': sign_notice(99999)}).status_code, 404)

    def test_other_collaboration_notice_is_404(self):
        other = VketCollaboration.objects.create(
            name='別コラボ', slug='other-og', phase=VketCollaboration.Phase.ENTRY_OPEN,
            period_start=self.collaboration.period_start, period_end=self.collaboration.period_end,
            registration_deadline=self.collaboration.registration_deadline, lt_deadline=self.collaboration.lt_deadline,
        )
        other_url = reverse('vket:notice_og_detail', kwargs={'pk': other.pk, 'notice_id': self.notice.pk})
        self.assertEqual(self.client.get(other_url, {'s': sign_notice(self.notice.pk)}).status_code, 404)
        response = self.client.get(
            reverse('vket:notice_list', kwargs={'pk': other.pk}), {'open': self.notice.pk, 's': sign_notice(self.notice.pk)},
        )
        self.assertNotContains(response, self.notice.title)

    def test_notice_without_sent_at_still_shows_signed_title(self):
        # 作成画面は sent_at を設定しないので、未設定のお知らせでも署名付きならタイトルを出す。
        VketNotice.objects.filter(pk=self.notice.pk).update(sent_at=None)
        response = self.client.get(self.image_url(self.notice), {'s': sign_notice(self.notice.pk)})
        self.assertEqual(response.status_code, 200)
        response = self.client.get(self.url('notice_list'), {'open': self.notice.pk, 's': sign_notice(self.notice.pk)})
        self.assertContains(response, self.notice.title)

    def test_draft_png_denies_title_and_list_redirects(self):
        self.collaboration.phase = VketCollaboration.Phase.DRAFT
        self.collaboration.save(update_fields=['phase'])
        self.assertEqual(self.client.get(self.image_url(self.notice), {'s': sign_notice(self.notice.pk)}).status_code, 404)
        self.assertRedirects(self.client.get(self.image_url()), DEFAULT_NEWS_IMAGE_URL, fetch_redirect_response=False)

    def test_individual_fallback_still_requires_signature(self):
        self.set_settings({})
        self.assertEqual(self.client.get(self.image_url(self.notice)).status_code, 404)
        self.assertRedirects(self.client.get(self.image_url(self.notice), {'s': sign_notice(self.notice.pk)}), DEFAULT_NEWS_IMAGE_URL, fetch_redirect_response=False)

    def test_title_edit_changes_version(self):
        request = RequestFactory().get('/')
        first = notice_image_url(request, self.collaboration, self.notice.title, self.notice)
        second = notice_image_url(request, self.collaboration, '変更後のタイトル', self.notice)
        self.assertNotEqual(parse_qs(urlsplit(first).query)['v'], parse_qs(urlsplit(second).query)['v'])

    def test_long_title_png(self):
        VketNotice.objects.filter(pk=self.notice.pk).update(title='とても長い日本語のご案内と確認事項について。' * 8)
        self.assert_png(self.client.get(self.image_url(self.notice), {'s': sign_notice(self.notice.pk)}))

    def test_render_failure_redirects_and_logs_without_title_or_signature(self):
        with patch('vket.views.og.render_notice_png', side_effect=OSError(self.notice.title)):
            with self.assertLogs('vket.og', level='WARNING') as logs:
                response = self.client.get(self.image_url(self.notice), {'s': sign_notice(self.notice.pk)})
        self.assertRedirects(response, DEFAULT_NEWS_IMAGE_URL, fetch_redirect_response=False)
        self.assertIn('vket_og_render_failed', logs.output[0])
        self.assertNotIn(self.notice.title, logs.output[0])
        self.assertNotIn(sign_notice(self.notice.pk), logs.output[0])

    def test_remind_signature_and_authenticated_open(self):
        request = RequestFactory().get('/')
        url = _build_remind_text(request, self.notice, ['<@123>']).splitlines()[-1]
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query['open'], [str(self.notice.pk)])
        self.assertTrue(valid_notice_signature(self.notice.pk, query['s'][0]))
        self.assertEqual(url, notice_link(request, self.notice))
        self.client.force_login(self.owner)
        response = self.client.get(url)
        self.assertEqual(response.context['open_notice_id'], self.notice.pk)
        self.assertTrue(response.context['scroll_to_open'])

    def test_png_post_is_disallowed(self):
        self.assertEqual(self.client.post(self.image_url()).status_code, 405)
        self.assertEqual(self.client.post(self.image_url(self.notice)).status_code, 405)

    def test_title_html_is_escaped(self):
        title = '<script>"公開タイトル"</script>'
        VketNotice.objects.filter(pk=self.notice.pk).update(title=title)
        response = self.client.get(self.url('notice_list'), {'open': self.notice.pk, 's': sign_notice(self.notice.pk)})
        self.assertNotContains(response, title)
        self.assertContains(response, escape(title))

    def test_stage_register_anonymous_get_has_public_metadata(self):
        response = self.client.get(self.url('stage_register'))
        self.assert_private_shell(response)
        self.assertContains(response, f'http://testserver/static/{OG_IMAGES["stage_register"]}', count=2)
        self.assertContains(response, '<meta property="og:title" content="Vket テスト ステージ登録">', html=True)
        self.assertEqual(parse_qs(urlsplit(response.context['login_url']).query)['next'], [self.url('stage_register')])
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_stage_register_authenticated_get_has_same_metadata(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.url('stage_register'))
        self.assertContains(response, f'http://testserver/static/{OG_IMAGES["stage_register"]}', count=2)
        self.assertContains(response, '<meta property="og:title" content="Vket テスト ステージ登録">', html=True)
        self.assertEqual(response.context['login_url'], self.url('status'))

    def test_stage_register_post_and_draft_keep_login_requirement(self):
        self.assertEqual(self.client.post(self.url('stage_register')).status_code, 302)
        self.collaboration.phase = VketCollaboration.Phase.DRAFT
        self.collaboration.save(update_fields=['phase'])
        self.assertEqual(self.client.get(self.url('stage_register')).status_code, 302)

    def test_collaboration_detail_metadata_and_draft_visibility(self):
        response = self.client.get(self.url('detail'))
        self.assertContains(response, '<meta property="og:title" content="Vket テスト">', html=True)
        self.assertContains(response, f'http://testserver/static/{OG_IMAGES["detail"]}', count=2)
        self.assertContains(response, '<meta property="og:image:width" content="1200">', html=True)
        self.assertContains(response, '<meta property="og:image:height" content="630">', html=True)
        self.collaboration.phase = VketCollaboration.Phase.DRAFT
        self.collaboration.save(update_fields=['phase'])
        self.assertEqual(self.client.get(self.url('detail')).status_code, 404)

    def test_stage_and_detail_missing_or_invalid_images_use_default(self):
        for page in ('stage_register', 'detail'):
            for value in (None, '', [], '/absolute.jpg', '../secret.jpg', 'https://example.com/og.jpg',
                          'bad\\path.jpg', 'image.jpg?x=1'):
                self.set_settings({'og_images': {page: value}})
                response = self.client.get(self.url(page))
                self.assertContains(response, f'<meta property="og:image" content="{DEFAULT_NEWS_IMAGE_URL}">', html=True)
                self.assertContains(response, f'<meta name="twitter:image" content="{DEFAULT_NEWS_IMAGE_URL}">', html=True)


class NoticeTextLayoutTests(SimpleTestCase):
    def test_kinsoku_and_english_words(self):
        font = ImageFont.truetype(str(SHARED_ASSET_ROOT / 'fonts' / 'MochiyPopOne-Regular.ttf'), 40)
        for title in ('あいうえお、かきくけこ。さしすせそ）」』】・ーです', 'ご案内 VRChat Community 参加登録'):
            lines = _wrap_text(title, font, 360)
            self.assertEqual(''.join(lines).replace(' ', ''), title.replace(' ', ''))
            for line in lines:
                self.assertNotIn(line[0], NO_LINE_START)
                self.assertLessEqual(font.getlength(line), 360)
        self.assertIn('VRChat', _wrap_text('ああ VRChat', font, 210))

    def test_long_word_newlines_and_empty_title_are_renderable(self):
        for title in ('A' * 300, '\n'.join(['改行'] * 10), '', 'ご案内\r\n詳細\t説明'):
            with Image.open(BytesIO(render_notice_png(ASSET_ROOT / 'vket-2026-winter', title))) as image:
                self.assertEqual(image.size, (1200, 630))

    def test_nonexistent_base_is_invalid(self):
        class Collaboration:
            settings_json = {'og_images': {'notice_base': 'does-not-exist'}}
        self.assertIsNone(notice_base(Collaboration()))
