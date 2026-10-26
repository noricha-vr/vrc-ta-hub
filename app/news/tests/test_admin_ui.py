"""ニュース管理 UI の表示・送信契約・運営権限の回帰テスト。"""
from bs4 import BeautifulSoup
from django.test import TestCase
from django.urls import reverse

from news.models import Category, Post
from tests.factories import make_community, make_community_member, make_user


class NewsAdminUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = make_user(is_staff=True)
        cls.category = Category.objects.create(name='テスト分類', slug='admin-ui-test')
        cls.post = Post.objects.create(
            title='確認するニュース', slug='admin-ui-news', category=cls.category,
            body_markdown='本文', is_published=False,
        )

    def setUp(self):
        self.client.force_login(self.staff)

    def test_form_headings_and_save_contract(self):
        for name, args, title in (
            ('create', [], 'ニュースの作成: 新しい記事'),
            ('edit', [self.post.slug], f'ニュースの編集: {self.post.title}'),
        ):
            with self.subTest(name=name):
                response = self.client.get(reverse(f'news:{name}', args=args))
                self.assertTemplateUsed(response, 'ta_hub/admin_base.html')
                soup = BeautifulSoup(response.content, 'html.parser')
                self.assertEqual(soup.h1.get_text(strip=True), title)
                form = soup.select_one('#news-form')
                self.assertEqual(form['method'], 'post')
                self.assertEqual(form['enctype'], 'multipart/form-data')
                self.assertIsNone(form.get('action'))
                self.assertEqual(
                    {node['name'] for node in form.select('[name]')},
                    {'csrfmiddlewaretoken', 'title', 'category', 'body_markdown',
                     'slug', 'meta_description', 'thumbnail', 'is_published'},
                )
                self.assertIsNotNone(soup.select_one('.stickybar button[type="submit"][form="news-form"]'))
                self.assertIsNotNone(soup.select_one('section[aria-labelledby="news-publication"] [name="is_published"]'))
                self.assertEqual(len(soup.select('.admin-toc a')), 3)
                self.assertIn(' - ', soup.title.get_text())
                self.assertNotContains(response, 'confirm(')

    def test_delete_uses_open_targeted_confirmation(self):
        url = reverse('news:delete', args=[self.post.slug])
        response = self.client.get(url)
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertEqual(soup.h1.get_text(strip=True), f'ニュースの削除: {self.post.title}')
        panel = soup.select_one('#delete-news.confirm-panel')
        self.assertFalse(panel.has_attr('hidden'))
        self.assertIn(self.post.title, panel.get_text())
        self.assertEqual(panel.form['action'], url)
        self.assertNotContains(response, 'confirm(')
        self.assertNotIn('text-danger', soup.h1.get('class', []))

    def test_invalid_post_retains_errors_and_publish_choice(self):
        response = self.client.post(reverse('news:create'), {'is_published': 'on'})
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertIsNotNone(soup.select_one('.admin-form-errors a[href="#id_title"]'))
        self.assertTrue(soup.select_one('[name="is_published"]').has_attr('checked'))
        self.assertIsNotNone(soup.select_one('button[form="news-form"]'))

    def test_publish_and_unpublish_keep_existing_save_behavior(self):
        url = reverse('news:edit', args=[self.post.slug])
        fields = {
            'title': self.post.title, 'slug': self.post.slug,
            'category': self.category.pk, 'body_markdown': '更新した本文',
        }
        for published in (True, False):
            with self.subTest(published=published):
                data = {**fields, **({'is_published': 'on'} if published else {})}
                response = self.client.post(url, data)
                self.assertRedirects(response, reverse('news:detail', args=[self.post.slug]))
                self.post.refresh_from_db()
                self.assertEqual(self.post.is_published, published)
                self.assertEqual(self.post.published_at is not None, published)

    def test_owner_community_staff_and_self_only_remain_forbidden(self):
        owner = make_user(user_name='owner', email='owner@example.com')
        member = make_user(user_name='member', email='member@example.com')
        personal = make_user(user_name='personal', email='personal@example.com')
        community = make_community(owner=owner)
        make_community_member(community, member)
        for user in (owner, member, personal):
            self.client.force_login(user)
            for name, args in (('create', []), ('edit', [self.post.slug]), ('delete', [self.post.slug])):
                url = reverse(f'news:{name}', args=args)
                with self.subTest(user=user.pk, name=name):
                    self.assertEqual(self.client.get(url).status_code, 403)
                    self.assertEqual(self.client.post(url, {}).status_code, 403)
        self.post.refresh_from_db()
        self.assertEqual(self.post.body_markdown, '本文')

    def test_superuser_without_staff_retains_news_specific_restriction(self):
        # ニュースは is_staff のみ。告知予約の is_staff または superuser と区別する。
        user = make_user(user_name='root', email='root@example.com', is_superuser=True, is_staff=False)
        self.client.force_login(user)
        for name, args in (('create', []), ('edit', [self.post.slug]), ('delete', [self.post.slug])):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(f'news:{name}', args=args)).status_code, 403)
