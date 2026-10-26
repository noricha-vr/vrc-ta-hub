"""告知予約の共通 UI・確認対象・入力と権限の回帰テスト。"""
from datetime import timedelta

from bs4 import BeautifulSoup
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from announcement.models import DiscordScheduledMessage
from tests.factories import make_community, make_community_member, make_user

from ._helpers import FAKE_WEBHOOK_TOKEN, FAKE_WEBHOOK_URL, jst, make_message

Status = DiscordScheduledMessage.Status


@override_settings(DISCORD_ANNOUNCE_WEBHOOK_URL=FAKE_WEBHOOK_URL)
class AnnouncementAdminUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = make_user(is_staff=True)
        cls.message = make_message(body='対象を確認する告知', scheduled_at=jst(2026, 12, 2, 20))

    def setUp(self):
        self.client.force_login(self.staff)

    def detail_url(self, message=None):
        return reverse('announcement:discord_detail', args=[(message or self.message).pk])

    def test_headings_and_shared_base_do_not_expose_connection_or_lease_secrets(self):
        secret = 'dummy-sending-lease-token'
        sending = make_message(lease_token=secret, lease_expires_at=timezone.now() + timedelta(minutes=5))
        cases = (
            (reverse('announcement:discord_list'), 'Discord 告知の予約: 告知チャンネル'),
            (reverse('announcement:discord_create'), 'Discord 告知の予約作成: 告知チャンネル'),
            (self.detail_url(), 'Discord 告知の予約詳細: 2026/12/02（水）20:00'),
            (self.detail_url(sending), None),
        )
        for url, heading in cases:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertTemplateUsed(response, 'ta_hub/admin_base.html')
                soup = BeautifulSoup(response.content, 'html.parser')
                if heading:
                    self.assertEqual(soup.h1.get_text(strip=True), heading)
                self.assertContains(response, '運営用')
                for value in (FAKE_WEBHOOK_URL, FAKE_WEBHOOK_TOKEN, secret, 'confirm('):
                    self.assertNotContains(response, value)

    def test_create_form_and_fixed_save_button_keep_field_contract(self):
        response = self.client.get(reverse('announcement:discord_create'))
        soup = BeautifulSoup(response.content, 'html.parser')
        form = soup.select_one('#announce-create-form')
        self.assertEqual(form['method'], 'post')
        self.assertIsNone(form.get('action'))
        self.assertEqual(
            {node['name'] for node in form.select('[name]')},
            {'csrfmiddlewaretoken', 'body', 'mention', 'scheduled_at'},
        )
        self.assertEqual([h.get_text(strip=True) for h in form.select('h2')], ['送信先', '内容', '日時'])
        self.assertEqual(form.select_one('[name="scheduled_at"]')['type'], 'datetime-local')
        self.assertIsNotNone(form.select_one('[data-role="discord-body"][data-max-length="2000"]'))
        self.assertIsNotNone(soup.select_one('.stickybar button[form="announce-create-form"]'))
        self.assertContains(response, 'ta_hub/js/admin-ui.js')

    def test_cancel_panel_identifies_target_and_posts_to_existing_url(self):
        response = self.client.get(self.detail_url())
        soup = BeautifulSoup(response.content, 'html.parser')
        panel = soup.select_one('#cancel-announcement.confirm-panel')
        self.assertTrue(panel.has_attr('hidden'))
        self.assertIn(f'#{self.message.pk}', panel.get_text())
        self.assertIn('2026/12/02（水）20:00', panel.get_text())
        self.assertIn(self.message.body, panel.get_text())
        self.assertEqual(panel.form['action'], reverse('announcement:discord_cancel', args=[self.message.pk]))
        self.assertIsNotNone(soup.select_one('[data-confirm-panel="cancel-announcement"]'))
        self.assertIsNotNone(soup.select_one('#announce-edit.collapse #announce-edit-form'))
        self.assertIsNotNone(soup.select_one('#announce-edit button[form="announce-edit-form"]'))
        self.assertIsNone(panel.find_parent('form'))

    def test_resend_warning_is_immediately_before_final_buttons(self):
        message = make_message(status=Status.FAILED, body='再送の対象', last_error='送信に失敗')
        response = self.client.get(self.detail_url(message))
        soup = BeautifulSoup(response.content, 'html.parser')
        panel = soup.select_one('#resend-announcement.confirm-panel')
        self.assertTrue(panel.has_attr('hidden'))
        self.assertIn(f'#{message.pk}', panel.get_text())
        self.assertIn('再送の対象', panel.get_text())
        self.assertEqual(panel.form['action'], reverse('announcement:discord_resend', args=[message.pk]))
        buttons = panel.select_one('button[type="submit"]').parent
        warning = buttons.find_previous_sibling('p')
        self.assertIn('再送すると予約中に戻ります', warning.get_text())
        self.assertIn('1 分ほどで告知チャンネルへ送ります', warning.get_text())
        self.assertIsNone(panel.find_parent('form'))

    def test_read_only_and_retry_states_keep_available_actions(self):
        cases = (
            ({'status': Status.SENT}, '送信済み', False, False),
            ({'status': Status.CANCELED}, '取り消し', False, False),
            ({'lease_token': 'dummy-lease'}, '送信処理中', False, False),
            ({'status': Status.FAILED}, '失敗', False, True),
            ({'attempt_count': 1, 'next_attempt_at': timezone.now() + timedelta(minutes=2)}, '再試行待ち', True, False),
        )
        for fields, label, editable, resendable in cases:
            with self.subTest(label=label):
                message = make_message(**fields)
                response = self.client.get(self.detail_url(message))
                soup = BeautifulSoup(response.content, 'html.parser')
                self.assertIn(label, soup.select_one('[data-testid="announce-status"]').get_text())
                self.assertEqual(soup.select_one('#announce-edit-form') is not None, editable)
                self.assertEqual(soup.select_one('#cancel-announcement') is not None, editable)
                self.assertEqual(soup.select_one('#resend-announcement') is not None, resendable)

    def test_invalid_edit_stays_open_and_preserves_saved_target(self):
        response = self.client.post(self.detail_url(), {'body': '', 'scheduled_at': '', 'mention': 'none'})
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertIn('show', soup.select_one('#announce-edit')['class'])
        self.assertNotIn('show', soup.select_one('#announce-view')['class'])
        self.assertIsNotNone(soup.select_one('.admin-form-errors a[href="#id_body"]'))
        self.assertIn(self.message.body, soup.select_one('#cancel-announcement').get_text())
        self.assertIsNotNone(soup.select_one('#announce-edit button[form="announce-edit-form"]'))

    def test_community_roles_and_creator_do_not_gain_operator_permissions(self):
        owner = make_user(user_name='owner', email='owner@example.com')
        member = make_user(user_name='member', email='member@example.com')
        creator = make_user(user_name='creator', email='creator@example.com')
        community = make_community(owner=owner)
        make_community_member(community, member)
        self.message.created_by = creator
        self.message.save(update_fields=['created_by'])
        for user in (owner, member, creator):
            self.client.force_login(user)
            for url in (reverse('announcement:discord_list'), reverse('announcement:discord_create'), self.detail_url()):
                with self.subTest(user=user.pk, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)
            for url in (reverse('announcement:discord_create'), self.detail_url(),
                        reverse('announcement:discord_cancel', args=[self.message.pk]),
                        reverse('announcement:discord_resend', args=[self.message.pk])):
                with self.subTest(user=user.pk, post=url):
                    self.assertEqual(self.client.post(url, {}).status_code, 403)
        self.message.refresh_from_db()
        self.assertEqual(self.message.status, Status.SCHEDULED)
        self.assertEqual(self.message.body, '対象を確認する告知')
