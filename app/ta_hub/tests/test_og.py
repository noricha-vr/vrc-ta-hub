"""公開ページの OGP・対象の公開範囲・共通描画キャッシュ。"""
from hashlib import sha256
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.template import Context, Template
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import resolve, reverse
from PIL import Image

from community.models import Community
from guide.views import load_markdown_content
from news.models import Category
from ta_hub.og import application_text
from ta_hub.og_image import ASSET_ROOT, _render_text_png_cached, render_text_png
from tests.factories import make_community, make_event, make_user
from website.constants import DEFAULT_NEWS_IMAGE_URL


class MetaParser(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.values = {}
        self.feed(content.decode())

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta':
            key = attrs.get('property', attrs.get('name'))
            self.values.setdefault(key, []).append(attrs.get('content'))


@override_settings(DISCORD_AUTH_REQUIRED=False)
class HubOgTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user(is_superuser=True, is_staff=True)
        cls.community = make_community(name='技術の集会', owner=cls.user, accepts_lt_application=True)
        cls.event = make_event(cls.community)
        cls.category = Category.objects.create(name='お知らせ', slug='og-news')

    def assert_image(self, response, url):
        self.assertEqual(response.status_code, 200)
        meta = MetaParser(response.content).values
        self.assertEqual(meta['og:image'], [url])
        self.assertEqual(meta['twitter:image'], [url])
        self.assertEqual(meta['og:image:width'], ['1200'])
        self.assertEqual(meta['og:image:height'], ['630'])

    def assert_png(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertEqual(response['Cache-Control'], 'public, max-age=86400')
        with Image.open(BytesIO(response.content)) as image:
            self.assertEqual(image.format, 'PNG')
            self.assertEqual(image.size, (1200, 630))

    def application_paths(self):
        return [
            reverse('event:lt_application_create', kwargs={'community_pk': self.community.pk}),
            reverse('event:lt_application_create_for_event', kwargs={
                'community_pk': self.community.pk, 'event_pk': self.event.pk,
            }),
        ]

    def test_fixed_images_for_each_public_page(self):
        pages = {
            '/event/list/': 'ta_hub/og/event-list.jpg',
            '/event/detail/history/': 'ta_hub/og/history.jpg',
            '/community/list/': 'ta_hub/og/community-list.jpg',
            '/community/archive/': 'ta_hub/og/community-archive.jpg',
            '/community/criteria/': 'ta_hub/og/community-criteria.jpg',
            '/account/register/': 'ta_hub/og/register.jpg',
            '/guide/': 'ta_hub/og/guide.jpg',
            '/about/': 'ta_hub/og/about.jpg',
            '/news/': 'ta_hub/og/news.jpg',
            f'/news/category/{self.category.slug}/': 'ta_hub/og/news.jpg',
            '/vket/': 'vket/og/list.jpg',
        }
        for path, image in pages.items():
            with self.subTest(path=path):
                self.assert_image(self.client.get(path), f'http://testserver/static/{image}')
        # 承認待ち一覧の閲覧権限は従来どおり。
        self.assertEqual(self.client.get('/community/waiting_list/').status_code, 302)
        self.client.force_login(self.user)
        self.assert_image(self.client.get('/community/waiting_list/'),
                          'http://testserver/static/ta_hub/og/community-waiting.jpg')

    def test_news_preserves_base_title_and_description(self):
        response = self.client.get('/news/')
        # ニュース一覧にはタイトル指定が無いため base の既定を使う。
        meta = MetaParser(response.content).values
        self.assertEqual(meta['og:title'], ['VRChat 技術・学術系イベントHub - VRChat技術学術系集会の世界へ！ようこそ！'])
        self.assertIn('最新イベント情報', meta['description'][0])

    def test_community_png_is_public_and_1200_by_630(self):
        self.assert_png(self.client.get(reverse('ta_hub:og_community', kwargs={'pk': self.community.pk})))

    def test_application_png_is_public_and_1200_by_630(self):
        self.assert_png(self.client.get(reverse('ta_hub:og_lt_apply', kwargs={'pk': self.community.pk})))

    def test_guide_png_and_metadata_use_frontmatter_title(self):
        path = 'community/create'
        _, frontmatter = load_markdown_content(path)
        route = reverse('ta_hub:og_guide', kwargs={'path': path})
        expected = f'http://testserver{route}?v={sha256(frontmatter["title"].encode()).hexdigest()[:8]}'
        self.assert_image(self.client.get(f'/guide/{path}/'), expected)
        with patch('ta_hub.views_og.render_text_png', wraps=render_text_png) as render:
            self.assert_png(self.client.get(expected))
        render.assert_called_once_with(ASSET_ROOT / 'guide', frontmatter['title'])

    def test_missing_or_unapproved_communities_are_404(self):
        for route in ('ta_hub:og_community', 'ta_hub:og_lt_apply'):
            self.assertEqual(self.client.get(reverse(route, kwargs={'pk': 999999})).status_code, 404)
            for status in ('pending', 'rejected', 'closed'):
                Community.objects.filter(pk=self.community.pk).update(status=status)
                self.assertEqual(self.client.get(reverse(route, kwargs={'pk': self.community.pk})).status_code, 404)

    def test_application_png_requires_acceptance(self):
        Community.objects.filter(pk=self.community.pk).update(accepts_lt_application=False)
        self.assertEqual(self.client.get(reverse('ta_hub:og_lt_apply', kwargs={'pk': self.community.pk})).status_code, 404)
        self.assert_png(self.client.get(reverse('ta_hub:og_community', kwargs={'pk': self.community.pk})))

    def test_invalid_guide_paths_are_404(self):
        for path in ('missing', '../about', 'community/../community/create',
                     '../../AGENTS', r'community\create', '%2e%2e/about'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(f'/og/guide/{path}.png').status_code, 404)

    def test_guide_alias_uses_existing_loader(self):
        self.assert_png(self.client.get(reverse('ta_hub:og_guide', kwargs={'path': 'event/auto-post'})))
        # 古いファイル名しかない配置でも、HTML と同じ alias を使う。
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'event').mkdir()
            (root / 'event/auto-tweet.md').write_text('---\ntitle: 自動投稿\n---\n本文')
            with patch('guide.views.GUIDE_ROOT', root):
                self.assert_png(self.client.get(reverse('ta_hub:og_guide', kwargs={'path': 'event/auto-post'})))

    def test_guide_image_reads_title_without_converting_body(self):
        # 画像に要るのはタイトルだけなので、匿名アクセスのたびに本文を HTML へ変換しない。
        with patch('guide.views.convert_markdown') as convert:
            self.assert_png(self.client.get(reverse('ta_hub:og_guide', kwargs={'path': 'event/auto-post'})))
        convert.assert_not_called()

    def test_concurrent_requests_render_same_image_once(self):
        import threading
        _render_text_png_cached.cache_clear()
        with patch('ta_hub.og_image.Image.open', wraps=Image.open) as opened:
            threads = [
                threading.Thread(target=render_text_png, args=(ASSET_ROOT / 'community', '同時アクセス'))
                for _ in range(5)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(opened.call_count, 1)

    def test_image_routes_do_not_conflict_and_reject_post(self):
        for route, kwargs in (
            ('ta_hub:og_community', {'pk': self.community.pk}),
            ('ta_hub:og_lt_apply', {'pk': self.community.pk}),
            ('ta_hub:og_guide', {'path': 'community/create'}),
        ):
            path = reverse(route, kwargs=kwargs)
            self.assertEqual(resolve(path).view_name, route)
            self.assertEqual(self.client.post(path).status_code, 405)

    def test_same_text_and_different_query_reuse_cache(self):
        for route, kwargs in (
            ('ta_hub:og_community', {'pk': self.community.pk}),
            ('ta_hub:og_lt_apply', {'pk': self.community.pk}),
            ('ta_hub:og_guide', {'path': 'community/create'}),
        ):
            _render_text_png_cached.cache_clear()
            with patch('ta_hub.og_image.Image.open', wraps=Image.open) as opened:
                responses = [self.client.get(reverse(route, kwargs=kwargs), {'v': v, 'title': v})
                             for v in ('a', 'b', 'c')]
            self.assertEqual(opened.call_count, 1)
            self.assertEqual(len({r.content for r in responses}), 1)
            for response in responses:
                self.assert_png(response)

    def test_image_render_failure_redirects_and_logs_only_id(self):
        for route, kwargs in (
            ('ta_hub:og_community', {'pk': self.community.pk}),
            ('ta_hub:og_lt_apply', {'pk': self.community.pk}),
            ('ta_hub:og_guide', {'path': 'community/create'}),
        ):
            with patch('ta_hub.views_og.render_text_png', side_effect=OSError('private failure')):
                with self.assertLogs('ta_hub.views_og', level='WARNING') as logs:
                    response = self.client.get(reverse(route, kwargs=kwargs))
            self.assertRedirects(response, DEFAULT_NEWS_IMAGE_URL, fetch_redirect_response=False)
            self.assertIn('hub_og_render_failed object_id=', logs.output[0])
            self.assertNotIn('private failure', logs.output[0])
            self.assertNotIn(self.community.name, logs.output[0])
            self.assertNotIn('community/create', logs.output[0])

    def test_community_without_poster_uses_generated_image(self):
        route = reverse('ta_hub:og_community', kwargs={'pk': self.community.pk})
        expected = f'http://testserver{route}?v={sha256(self.community.name.encode()).hexdigest()[:8]}'
        self.assert_image(self.client.get(reverse('community:detail', kwargs={'pk': self.community.pk})), expected)

    def test_existing_poster_is_preserved(self):
        Community.objects.filter(pk=self.community.pk).update(poster_image='posters/original.jpg')
        response = self.client.get(reverse('community:detail', kwargs={'pk': self.community.pk}))
        meta = MetaParser(response.content).values
        self.assertEqual(meta['og:image'], ['/media/posters/original.jpg'])
        self.assertEqual(meta['twitter:image'], ['/media/posters/original.jpg'])
        self.assertNotIn('/og/community/', response.content.decode())

    def test_anonymous_application_get_is_private_public_shell(self):
        for path in self.application_paths():
            response = self.client.get(path, {'q': '"><script>input</script>'})
            self.assertContains(response, f'{self.community.name} 発表申込み')
            self.assertContains(response, '<meta name="robots" content="noindex,follow">', html=True)
            self.assertNotContains(response, '<script>input</script>')
            self.assertNotContains(response, 'name="theme"')
            self.assertEqual(response['X-Robots-Tag'], 'noindex')
            self.assertIn('private', response['Cache-Control'])
            self.assertIn('no-store', response['Cache-Control'])
            self.assertIn('Cookie', response['Vary'])
            self.assertEqual(parse_qs(urlsplit(response.context['login_url']).query)['next'],
                             [response.wsgi_request.get_full_path()])
            self.assert_image(response, response.context['og_image_url'])
            self.assert_png(self.client.get(response.context['og_image_url']))

    def test_noneligible_application_get_keeps_login_redirect(self):
        for values in ({'status': 'pending'}, {'status': 'rejected'}, {'status': 'closed'},
                       {'status': 'approved', 'accepts_lt_application': False}):
            Community.objects.filter(pk=self.community.pk).update(**values)
            for path in self.application_paths():
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.url.startswith('/account/login/?next='))

    def test_anonymous_post_and_head_keep_login_redirect(self):
        for path in self.application_paths():
            for method in (self.client.post, self.client.head):
                self.assertEqual(method(path).status_code, 302)

    def test_authenticated_application_get_has_same_metadata_and_form(self):
        for path in self.application_paths():
            public = self.client.get(path)
            self.client.force_login(self.user)
            response = self.client.get(path)
            self.assert_image(response, public.context['og_image_url'])
            self.assertContains(response, 'name="theme"')
            self.client.logout()

    def test_name_edit_changes_image_version(self):
        path = self.application_paths()[0]
        before = self.client.get(path).context['og_image_url']
        Community.objects.filter(pk=self.community.pk).update(name='変更後の集会')
        after = self.client.get(path).context['og_image_url']
        self.assertNotEqual(before, after)
        self.assertEqual(parse_qs(urlsplit(before).query)['v'],
                         [sha256(application_text(self.community).encode()).hexdigest()[:8]])


class SharedOgTests(SimpleTestCase):
    def test_static_tag_handles_relative_and_cdn_urls(self):
        request = RequestFactory().get('/')
        template = Template("{% load og_tags %}{% og_static 'ta_hub/og/about.jpg' %}")
        for url, expected in (
            ('static/about.jpg', 'http://testserver/static/about.jpg'),
            ('/static/about.jpg', 'http://testserver/static/about.jpg'),
            ('https://cdn.example.com/og.jpg', 'https://cdn.example.com/og.jpg'),
        ):
            with patch('ta_hub.og_image.static', return_value=url):
                self.assertEqual(template.render(Context({'request': request})), expected)

    def test_each_background_renders_short_and_long_text(self):
        for background in ('lt-apply', 'community', 'guide'):
            for text in ('技術集会', '技術と学術を楽しく学ぶとても長い名前の集会 VRChat Community です。' * 8):
                with Image.open(BytesIO(render_text_png(ASSET_ROOT / background, text))) as image:
                    self.assertEqual(image.size, (1200, 630))
                    self.assertEqual(image.format, 'PNG')

    def test_invalid_dimensions_or_text_box_are_rejected(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            Image.new('RGB', (1, 1)).save(base / 'base.png')
            with self.assertRaises(ValueError):
                render_text_png(base, 'invalid-size')
            Image.new('RGB', (1200, 630)).save(base / 'base.png')
            (base / 'base.json').write_text('{"text_box":[100,100,1200,630],"color":"#173a5e"}')
            with self.assertRaises(ValueError):
                render_text_png(base, 'invalid-box')
