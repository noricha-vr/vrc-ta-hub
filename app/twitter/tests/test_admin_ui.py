"""X 管理画面の表示契約と既存権限を確認する。"""
from unittest.mock import patch

from bs4 import BeautifulSoup
from django.urls import reverse
from django.utils import timezone

from tests.factories import make_discord_linked_user, make_event_detail
from twitter.models import TweetQueue
from twitter.tests.test_access_control import (
    OWNER, STAFF, SUPERUSER, TwitterAccessControlTestBase,
)


class TwitterAdminUITest(TwitterAccessControlTestBase):
    def setUp(self):
        super().setUp()
        self.operator = make_discord_linked_user(
            user_name='ui_operator', email='operator@example.com', is_staff=True,
        )
        self.personal = make_discord_linked_user(
            user_name='ui_personal', email='personal@example.com',
        )
        make_event_detail(self.event, applicant=self.personal)
        self.community.notification_webhook_url = 'https://discord.com/api/webhooks/000/DUMMY-PRIVATE'
        self.community.save(update_fields=['notification_webhook_url'])
        self.queue.generation_token = 'private-generation-token-for-ui-test'
        self.queue.save(update_fields=['generation_token'])

    def url(self, name, **kwargs):
        return reverse(f'twitter:{name}', kwargs=kwargs)

    def assert_admin_page(self, response, heading):
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'ta_hub/admin_base.html')
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertEqual([h.get_text(strip=True) for h in soup.select('h1')], [heading])
        self.assertNotContains(response, 'confirm(')
        self.assertNotContains(response, 'DUMMY-PRIVATE')
        self.assertNotContains(response, self.queue.generation_token)
        for form in soup.select('.hub-admin form'):
            self.assertIsNone(form.select_one('form'), 'フォームを入れ子にしない')
            self.assertIsNotNone(form.select_one('[name=csrfmiddlewaretoken]'))
        return soup

    def test_template_list_heading_grouping_and_inline_confirmation(self):
        for role in (OWNER, STAFF, SUPERUSER):
            with self.subTest(role=role):
                self.login_as(role)
                target = 'すべての集会' if role == SUPERUSER else self.community.name
                response = self.client.get(self.url('template_list'))
                soup = self.assert_admin_page(response, f'X 告知テンプレート: {target}')
                panel = soup.select_one(f'#delete-template-{self.template.pk}')
                self.assertIsNotNone(panel)
                self.assertTrue(panel.has_attr('hidden'))
                self.assertEqual(panel.form['action'], self.url('template_delete', pk=self.template.pk))
                self.assertIn(self.template.name, panel.get_text())
                self.assertNotContains(response, 'modal')
                if role == SUPERUSER:
                    headings = [h.get_text(strip=True) for h in soup.select('h2')]
                    self.assertIn(self.community.name, headings)
                    self.assertIn(self.other_community.name, headings)

    def test_template_create_and_update_use_target_and_save_bar(self):
        for role in (OWNER, STAFF, SUPERUSER):
            self.login_as(role)
            for name, url, heading in (
                ('create', self.url('template_create') + f'?community={self.community.pk}', '作成'),
                ('update', self.url('template_update', pk=self.template.pk), '編集'),
            ):
                with self.subTest(role=role, page=name):
                    response = self.client.get(url)
                    soup = self.assert_admin_page(response, f'X 告知テンプレートの{heading}: {self.community.name}')
                    form = soup.select_one('#twitter-template-form')
                    self.assertTrue(form.has_attr('data-admin-dirty'))
                    self.assertEqual({f['name'] for f in form.select('input[name], textarea[name]')},
                                     {'csrfmiddlewaretoken', 'name', 'template'})
                    self.assertIsNotNone(soup.select_one('.stickybar button[form=twitter-template-form]'))

    def test_update_heading_uses_object_instead_of_active_community(self):
        self.login_as(SUPERUSER)
        response = self.client.get(self.url('template_update', pk=self.template.pk))
        self.assert_admin_page(response, f'X 告知テンプレートの編集: {self.community.name}')

    def test_invalid_template_shows_errors_and_keeps_input(self):
        self.login_as(OWNER)
        response = self.client.post(self.url('template_create') + f'?community={self.community.pk}', {
            'name': '', 'template': '入力を保持する本文',
        })
        soup = self.assert_admin_page(response, f'X 告知テンプレートの作成: {self.community.name}')
        self.assertIsNotNone(soup.select_one('.admin-form-errors'))
        self.assertContains(response, '入力を保持する本文')

    def test_independent_delete_url_is_an_open_confirmation(self):
        for role in (OWNER, STAFF, SUPERUSER):
            with self.subTest(role=role):
                self.login_as(role)
                response = self.client.get(self.url('template_delete', pk=self.template.pk))
                soup = self.assert_admin_page(response, f'X 告知テンプレートの削除: {self.community.name}')
                panel = soup.select_one('#delete-template')
                self.assertFalse(panel.has_attr('hidden'))
                self.assertEqual(panel.form['action'], self.url('template_delete', pk=self.template.pk))

    @patch('twitter.views.generate_tweet', return_value='投稿文\n2 行目')
    def test_preview_labels_external_links_and_escaped_text(self, mock_generate):
        for role in (OWNER, STAFF, SUPERUSER):
            with self.subTest(role=role):
                self.login_as(role)
                response = self.client.get(self.url('tweet_event_with_template', event_pk=self.event.pk, template_pk=self.template.pk))
                soup = self.assert_admin_page(response, f'X 告知のプレビュー: {self.community.name}')
                self.assertEqual([h.get_text(strip=True) for h in soup.select('h2')],
                                 ['投稿文', '予約投稿の手順', '今すぐ投稿'])
                self.assertContains(response, 'このサイトでは予約は完了しません')
                self.assertEqual(soup.select_one('#tweet-text').get_text('\n'), '投稿文\n2 行目')
                for link in soup.select('.hub-admin a[target=_blank]'):
                    self.assertIn('noopener', link['rel'])
                    self.assertIn('↗', link.get_text())
                intent = soup.select_one('a[href^="https://twitter.com/intent/tweet"]')
                self.assertEqual(intent['href'], response.context['intent_url'])

    @patch('twitter.views.generate_tweet', return_value=None)
    def test_empty_preview_disables_copy_and_post_link(self, mock_generate):
        self.login_as(OWNER)
        response = self.client.get(self.url('tweet_event_with_template', event_pk=self.event.pk, template_pk=self.template.pk))
        soup = self.assert_admin_page(response, f'X 告知のプレビュー: {self.community.name}')
        self.assertContains(response, '投稿文を生成できませんでした')
        self.assertTrue(soup.select_one('#copy-button').has_attr('disabled'))
        self.assertIsNone(soup.select_one('a[href^="https://twitter.com/intent/"]'))

    def test_queue_list_viewer_and_superuser_actions(self):
        for role in (OWNER, STAFF, SUPERUSER):
            with self.subTest(role=role):
                self.login_as(role)
                response = self.client.get(self.url('tweet_queue_list'), {'status': 'ready'})
                target = 'すべての集会' if role == SUPERUSER else '所属する集会'
                soup = self.assert_admin_page(response, f'ポストキュー: {target}')
                self.assertIsNotNone(soup.select_one('.detail-chip'))
                self.assertEqual(soup.select_one('.admin-subnav [aria-current=page]')['href'], '?status=ready')
                self.assertEqual(bool(soup.select('[data-confirm-panel]')), role == SUPERUSER)
                if role == SUPERUSER:
                    form = soup.select_one('.confirm-panel form')
                    self.assertEqual(form['action'], self.url('tweet_queue_detail', pk=self.queue.pk))
                    self.assertEqual(form.select_one('button[name=action]')['value'], 'post_now')
                    self.assertIn('btn-primary', form.select_one('button[name=action]')['class'])
                    self.assertIn(self.queue.generated_text, form.get_text())

    def test_queue_detail_is_readonly_except_for_superuser(self):
        for role in (OWNER, STAFF, SUPERUSER):
            with self.subTest(role=role):
                self.login_as(role)
                response = self.client.get(self.url('tweet_queue_detail', pk=self.queue.pk))
                soup = self.assert_admin_page(response, f'ポストキュー #{self.queue.pk}: {self.community.name}')
                is_superuser = role == SUPERUSER
                for field_name in ('generated_text', 'image_url', 'scheduled_at'):
                    field = soup.select_one(f'[name={field_name}]')
                    self.assertEqual(field.has_attr('readonly'), not is_superuser)
                self.assertEqual(
                    soup.select_one('[name=scheduled_at]')['value'],
                    timezone.localtime(self.queue.scheduled_at).strftime('%Y-%m-%dT%H:%M'),
                )
                self.assertEqual(bool(soup.select('.stickybar')), is_superuser)
                self.assertEqual(bool(soup.select('[data-confirm-panel]')), is_superuser)
                if is_superuser:
                    self.assertEqual(soup.select_one('#tweet-queue-form [name=action]')['value'], 'update')
                    self.assertEqual(soup.select_one('#post-queue button[name=action]')['value'], 'post_now')
                    self.assertIn('btn-primary', soup.select_one('#post-queue button[name=action]')['class'])
                    self.assertIn('btn-danger', soup.select_one('#delete-queue button[name=action]')['class'])
                    self.assertEqual(soup.select_one('#delete-queue button[name=action]')['value'], 'delete')
                    self.assertIn(self.queue.generated_text, soup.select_one('#post-queue').get_text())
                else:
                    self.assertContains(response, '閲覧専用')
                    self.assertFalse(soup.select('.hub-admin form'))

    def test_queue_states_show_status_error_and_retry(self):
        self.login_as(SUPERUSER)
        for status, label in TweetQueue.STATUS_CHOICES:
            with self.subTest(status=status):
                self.queue.status = status
                self.queue.error_message = 'テスト用の理由' if status in ('failed', 'skipped', 'generation_failed') else ''
                self.queue.save(update_fields=['status', 'error_message'])
                response = self.client.get(self.url('tweet_queue_detail', pk=self.queue.pk))
                soup = self.assert_admin_page(response, f'ポストキュー #{self.queue.pk}: {self.community.name}')
                self.assertContains(response, label)
                self.assertEqual(bool(soup.select('button[value=retry]')), status in ('generation_failed', 'generating'))
                self.assertEqual(bool(soup.select('#post-queue')), status != 'posted')
                if self.queue.error_message:
                    self.assertContains(response, self.queue.error_message)

    def test_empty_lists(self):
        self.login_as(OWNER)
        self.template.delete()
        self.queue.delete()
        self.assertContains(self.client.get(self.url('template_list')), 'テンプレートがありません')
        self.assertContains(self.client.get(self.url('tweet_queue_list')), 'ポストキューがありません')

    @patch('twitter.views.generate_tweet')
    def test_operator_and_personal_user_keep_existing_access(self, mock_generate):
        for user in (self.operator, self.personal):
            with self.subTest(user=user.user_name):
                self.client.force_login(user)
                response = self.client.get(self.url('template_list'))
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, self.template.name)
                self.assertNotContains(response, '新規作成')
                urls = (
                    self.url('template_create') + f'?community={self.community.pk}',
                    self.url('template_update', pk=self.template.pk),
                    self.url('template_delete', pk=self.template.pk),
                    self.url('tweet_event_with_template', event_pk=self.event.pk, template_pk=self.template.pk),
                    self.url('tweet_queue_list'),
                    self.url('tweet_queue_detail', pk=self.queue.pk),
                )
                for url in urls:
                    self.assertEqual(self.client.get(url).status_code, 403, url)
        mock_generate.assert_not_called()
