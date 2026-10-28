"""Bot 導入の認可・権限・履歴と管理画面。外部通信は全てモックする。"""
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import requests
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from community.models import BotInstallation, CommunityMember
from community.views.bot_install import STATE_SESSION_KEY
from tests.factories import make_community, make_community_member, make_discord_linked_user


@override_settings(DISCORD_BOT_CLIENT_ID='synthetic-bot-client', DISCORD_BOT_CLIENT_SECRET='synthetic-secret')
class BotInstallTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_discord_linked_user(user_name='owner', email='owner@example.com')
        cls.staff = make_discord_linked_user(user_name='staff', email='staff@example.com')
        cls.other = make_discord_linked_user(user_name='other', email='other@example.com')
        cls.community = make_community(owner=cls.owner)
        cls.second = make_community(name='Second Community', owner=cls.owner)
        make_community_member(cls.community, cls.staff)

    def setUp(self):
        self.client.force_login(self.owner)
        self.start_url = reverse('community:bot_install_start', args=[self.community.pk])
        self.callback_url = reverse('community:bot_install_callback')
        self.settings_url = reverse('community:settings')

    def begin(self, community=None):
        community = community or self.community
        response = self.client.post(reverse('community:bot_install_start', args=[community.pk]))
        self.assertEqual(response.status_code, 302)
        return parse_qs(urlparse(response.url).query)['state'][0]

    def callback(self, state, **extra):
        return self.client.get(self.callback_url, {'state': state, 'code': 'synthetic-code', **extra})

    def installation(self, **extra):
        return BotInstallation.objects.create(
            community=self.community, guild_id='123456789012345678', added_by=self.owner, **extra,
        )

    def configure_exchange(self, post, guild_id='123456789012345678'):
        post.return_value.status_code = 200
        post.return_value.json.return_value = {'guild': {'id': guild_id}, 'access_token': 'synthetic-token'}

    def test_authorize_url_uses_separate_app_and_fixed_callback(self):
        state = self.begin()
        saved = self.client.session[STATE_SESSION_KEY]
        self.assertEqual(saved['community_id'], self.community.pk)
        self.assertEqual(saved['user_id'], self.owner.pk)
        self.assertGreater(len(state), 32)
        self.assertAlmostEqual(saved['expires_at'], timezone.now().timestamp() + 600, delta=5)
        response = self.client.post(self.start_url)
        query = parse_qs(urlparse(response.url).query)
        self.assertEqual(query['client_id'], ['synthetic-bot-client'])
        self.assertEqual(query['scope'], ['bot applications.commands'])
        self.assertEqual(query['permissions'], ['68608'])
        self.assertEqual(query['response_type'], ['code'])
        self.assertEqual(query['redirect_uri'], ['http://testserver/community/bot-install/callback/'])
        self.assertNotEqual(query['state'], [state])

    def test_staff_can_start_and_button_is_visible(self):
        self.client.force_login(self.staff)
        self.begin()
        self.assertContains(self.client.get(self.settings_url), 'Bot を追加')

    def test_start_and_remove_require_post_and_csrf(self):
        installation = self.installation()
        remove_url = reverse('community:bot_install_remove', args=[self.community.pk, installation.pk])
        self.assertEqual(self.client.get(self.start_url).status_code, 405)
        self.assertEqual(self.client.get(remove_url).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        self.assertEqual(client.post(self.start_url).status_code, 403)
        self.assertEqual(client.post(remove_url).status_code, 403)

    def test_anonymous_cannot_start_or_callback(self):
        self.client.logout()
        self.assertEqual(self.client.post(self.start_url).status_code, 302)
        self.assertEqual(self.callback('random').status_code, 302)
        self.assertFalse(BotInstallation.objects.exists())

    def test_nonmanager_cannot_start_or_see_button(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(self.start_url).status_code, 403)
        self.assertNotContains(self.client.get(self.settings_url, follow=True), 'Bot を追加')

    def test_global_staff_without_membership_cannot_start(self):
        self.other.is_staff = True
        self.other.save(update_fields=['is_staff'])
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(self.start_url).status_code, 403)

    @patch('community.views.bot_install.requests.post')
    def test_invalid_states_are_rejected_without_api_call(self, post):
        for variant in ('missing', 'mismatch', 'unicode', 'user', 'community', 'expired'):
            with self.subTest(variant=variant):
                state = self.begin()
                session = self.client.session
                saved = session[STATE_SESSION_KEY]
                if variant == 'user':
                    saved['user_id'] = self.other.pk
                elif variant == 'community':
                    session['active_community_id'] = self.second.pk
                elif variant == 'expired':
                    saved['expires_at'] = timezone.now().timestamp() - 1
                session[STATE_SESSION_KEY] = saved
                session.save()
                returned_state = {'missing': '', 'mismatch': 'wrong', 'unicode': '不正'}.get(variant, state)
                self.assertEqual(self.callback(returned_state).status_code, 403)
                self.assertNotIn(STATE_SESSION_KEY, self.client.session)
        post.assert_not_called()
        self.assertFalse(BotInstallation.objects.exists())

    @patch('community.views.bot_install.requests.post')
    def test_state_from_another_session_is_rejected(self, post):
        state = self.begin()
        another_client = Client()
        another_client.force_login(self.owner)
        response = another_client.get(self.callback_url, {'state': state, 'code': 'synthetic-code'})
        self.assertEqual(response.status_code, 403)
        post.assert_not_called()

    @patch('community.views.bot_install.requests.post')
    def test_nonmanager_callback_is_rejected_even_with_matching_state(self, post):
        self.client.force_login(self.other)
        session = self.client.session
        session['active_community_id'] = self.community.pk
        session[STATE_SESSION_KEY] = {
            'state': 'synthetic-state', 'user_id': self.other.pk, 'community_id': self.community.pk,
            'expires_at': timezone.now().timestamp() + 600,
        }
        session.save()
        self.assertEqual(self.callback('synthetic-state').status_code, 403)
        post.assert_not_called()

    @patch('community.views.bot_install.requests.post')
    def test_membership_is_rechecked_at_callback(self, post):
        state = self.begin()
        CommunityMember.objects.filter(community=self.community, user=self.owner).delete()
        self.assertEqual(self.callback(state).status_code, 403)
        post.assert_not_called()

    @patch('community.views.bot_install.requests.post')
    def test_exchange_guild_is_authoritative_and_state_is_single_use(self, post):
        self.configure_exchange(post)
        state = self.begin()
        response = self.callback(state, guild_id='987654321098765432', permissions='8')
        self.assertRedirects(response, self.settings_url)
        record = BotInstallation.objects.get()
        self.assertEqual(record.guild_id, '123456789012345678')
        self.assertEqual(record.community, self.community)
        self.assertEqual(record.added_by, self.owner)
        self.assertEqual(record.status, BotInstallation.Status.INSTALLED)
        self.assertIsNone(record.configured_at)
        self.assertIsNotNone(record.installed_at)
        self.assertNotIn(STATE_SESSION_KEY, self.client.session)
        self.assertContains(self.client.get(self.settings_url), '/hub setup')
        kwargs = post.call_args.kwargs
        self.assertEqual(post.call_args.args[0], 'https://discord.com/api/oauth2/token')
        self.assertEqual(kwargs['data'], {
            'client_id': 'synthetic-bot-client', 'client_secret': 'synthetic-secret',
            'grant_type': 'authorization_code', 'code': 'synthetic-code',
            'redirect_uri': 'http://testserver/community/bot-install/callback/',
        })
        self.assertEqual(kwargs['timeout'], 10)
        self.assertFalse(kwargs['allow_redirects'])
        self.assertEqual(self.callback(state).status_code, 403)
        post.assert_called_once()

    @patch('community.views.bot_install.requests.post')
    def test_staff_can_complete_install(self, post):
        self.configure_exchange(post)
        self.client.force_login(self.staff)
        self.assertRedirects(self.callback(self.begin()), self.settings_url)
        self.assertEqual(BotInstallation.objects.get().added_by, self.staff)

    @patch('community.views.bot_install.requests.post')
    def test_cancel_and_missing_code_consume_state(self, post):
        for query in ({'error': 'access_denied'}, {}):
            with self.subTest(query=query):
                state = self.begin()
                self.assertRedirects(self.client.get(self.callback_url, {'state': state, **query}), self.settings_url)
                self.assertEqual(self.callback(state).status_code, 403)
        post.assert_not_called()

    @patch('community.views.bot_install.requests.post')
    def test_bad_exchange_does_not_save_query_guild_or_log_secrets(self, post):
        for error in (requests.Timeout('synthetic-secret'), requests.HTTPError('synthetic-code'), ValueError('synthetic-token')):
            with self.subTest(error=type(error)):
                self.configure_exchange(post)
                post.return_value.raise_for_status.side_effect = error
                state = self.begin()
                with self.assertLogs('community.views.bot_install', level='WARNING') as logs:
                    self.assertRedirects(self.callback(state, guild_id='123456789012345678'), self.settings_url)
                self.assertNotIn('synthetic-', '\n'.join(logs.output))
                self.assertNotIn(STATE_SESSION_KEY, self.client.session)
                self.assertFalse(BotInstallation.objects.exists())

    @patch('community.views.bot_install.requests.post')
    def test_invalid_guild_response_never_falls_back_to_query(self, post):
        payloads = ({}, [], {'guild': None}, {'guild': []}, {'guild': {}},
                    {'guild': {'id': 123456789012345678}}, {'guild': {'id': '123'}},
                    {'guild': {'id': '１２３４５６７８９０１２３４５６７８'}},
                    {'guild': {'id': '123456789012345678901'}})
        for payload in payloads:
            with self.subTest(payload=payload):
                self.configure_exchange(post)
                post.return_value.json.return_value = payload
                self.assertRedirects(self.callback(self.begin(), guild_id='123456789012345678'), self.settings_url)
                self.assertFalse(BotInstallation.objects.exists())

    @patch('community.views.bot_install.requests.post')
    def test_duplicate_other_community_rejected_then_removed_can_be_relinked(self, post):
        original = self.installation()
        self.configure_exchange(post)
        response = self.callback(self.begin(self.second))
        self.assertContains(self.client.get(response.url), '既に別の集会に紐づいています')
        self.assertEqual(BotInstallation.objects.count(), 1)
        self.client.post(reverse('community:bot_install_remove', args=[self.community.pk, original.pk]))
        original.refresh_from_db()
        self.assertEqual(original.status, BotInstallation.Status.REMOVED)
        self.assertIsNotNone(original.removed_at)
        self.assertRedirects(self.callback(self.begin(self.second)), self.settings_url)
        self.assertEqual(BotInstallation.objects.count(), 2)
        self.assertTrue(BotInstallation.objects.filter(community=self.second, status='installed').exists())

    @patch('community.views.bot_install.requests.post')
    def test_reauthorize_same_community_preserves_configured_status(self, post):
        original = self.installation(status='configured', configured_at=timezone.now())
        self.configure_exchange(post)
        self.assertRedirects(self.callback(self.begin()), self.settings_url)
        original.refresh_from_db()
        self.assertEqual(original.status, 'configured')
        self.assertEqual(BotInstallation.objects.count(), 1)

    @patch('community.views.bot_install.requests.post')
    def test_database_race_is_handled_as_duplicate(self, post):
        self.configure_exchange(post)
        state = self.begin()
        with patch('community.views.bot_install.BotInstallation.objects.create', side_effect=IntegrityError):
            self.assertContains(self.client.get(self.callback(state).url), '既に別の集会に紐づいています')

    def test_database_enforces_active_unique_even_without_partial_indexes(self):
        self.installation()
        # SQLite の部分インデックスを外し、MySQL と同じ生成列の制約だけで検証する。
        with connection.cursor() as cursor:
            cursor.execute('DROP INDEX bot_installation_unique_active_guild')
        try:
            with self.assertRaises(IntegrityError), transaction.atomic():
                BotInstallation.objects.create(community=self.second, guild_id='123456789012345678', added_by=self.owner)
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    'CREATE UNIQUE INDEX bot_installation_unique_active_guild '
                    'ON community_botinstallation (guild_id) WHERE NOT (status = \'removed\')'
                )
        record = BotInstallation.objects.get()
        self.assertEqual(record.active_guild_id, record.guild_id)
        record.status = 'removed'
        record.removed_at = timezone.now()
        record.save()
        record.refresh_from_db()
        self.assertIsNone(record.active_guild_id)
        BotInstallation.objects.create(community=self.second, guild_id=record.guild_id, added_by=self.owner)

    def test_guild_validator_rejects_invalid_ids(self):
        for value in ('123', '12345678901234567x', '１２３４５６７８９０１２３４５６７８', '123456789012345678\n'):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                BotInstallation(community=self.community, guild_id=value, added_by=self.owner).full_clean()

    def test_remove_rechecks_community_and_permission(self):
        record = self.installation()
        url = reverse('community:bot_install_remove', args=[self.community.pk, record.pk])
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(reverse('community:bot_install_remove', args=[self.second.pk, record.pk])).status_code, 404)
        self.client.force_login(self.staff)
        self.assertRedirects(self.client.post(url), self.settings_url)
        record.refresh_from_db()
        self.assertEqual(record.status, 'removed')
        removed_at = record.removed_at
        self.client.post(url)
        record.refresh_from_db()
        self.assertEqual(record.removed_at, removed_at)

    def test_management_shows_all_states_and_confirmation(self):
        self.assertContains(self.client.get(self.settings_url), '未追加')
        record = self.installation()
        for status, label in BotInstallation.Status.choices:
            with self.subTest(status=status):
                record.status = status
                record.save()
                response = self.client.get(self.settings_url)
                self.assertContains(response, label)
                self.assertContains(response, record.guild_id)
                if status != 'removed':
                    self.assertContains(response, f'id="remove-bot-{record.pk}"')
                    self.assertContains(response, '紐づけを外す…')
                else:
                    self.assertNotContains(response, '紐づけを外す…')

    def test_unconfigured_bot_app_hides_button_and_blocks_start(self):
        for values in ({'DISCORD_BOT_CLIENT_ID': ''}, {'DISCORD_BOT_CLIENT_SECRET': ''}):
            with self.subTest(values=values), override_settings(**values):
                self.assertNotContains(self.client.get(self.settings_url), 'Bot を追加')
                self.assertRedirects(self.client.post(self.start_url), self.settings_url)
                self.assertNotIn(STATE_SESSION_KEY, self.client.session)

    @patch('community.views.bot_install.requests.post')
    def test_bot_disabled_between_start_and_callback(self, post):
        state = self.begin()
        with override_settings(DISCORD_BOT_CLIENT_SECRET=''):
            self.assertRedirects(self.callback(state), self.settings_url)
        post.assert_not_called()
        self.assertNotIn(STATE_SESSION_KEY, self.client.session)
