"""Bot 導入の認可・権限・履歴と管理画面。外部通信は全てモックする。"""
import base64
import hashlib
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import Client, TestCase, override_settings
from django.template.loader import render_to_string
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

    def configure_exchange(self, post, guild_id='123456789012345678', **guild_data):
        post.return_value.status_code = 200
        post.return_value.json.return_value = {
            'guild': {'id': guild_id, **guild_data}, 'access_token': 'synthetic-token',
        }

    def bot_section(self):
        response = self.client.get(self.settings_url)
        self.assertEqual(response.status_code, 200)
        return BeautifulSoup(response.content, 'html.parser').select_one('#bot-install')

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
        verifier = self.client.session[STATE_SESSION_KEY]['code_verifier']
        self.assertGreaterEqual(len(verifier), 43)
        self.assertEqual(query['code_challenge_method'], ['S256'])
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        self.assertEqual(query['code_challenge'], [expected])

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
                if variant in ('missing', 'mismatch', 'unicode'):
                    # 一致しない state では、進行中の正規フローを消さない。
                    self.assertIn(STATE_SESSION_KEY, self.client.session)
                else:
                    self.assertNotIn(STATE_SESSION_KEY, self.client.session)
        post.assert_not_called()
        self.assertFalse(BotInstallation.objects.exists())

    @patch('community.views.bot_install.requests.post')
    def test_wrong_state_keeps_pending_flow(self, post):
        self.configure_exchange(post)
        state = self.begin()
        self.assertEqual(self.callback('forged-state').status_code, 403)
        self.assertRedirects(self.callback(state), self.settings_url)
        self.assertEqual(BotInstallation.objects.get().guild_id, '123456789012345678')

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
        verifier = self.client.session[STATE_SESSION_KEY]['code_verifier']
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
            'code_verifier': verifier,
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
    def test_callback_saves_normalized_guild_name_without_logging_it(self, post):
        for guild_data, expected in (
            ({'name': 'テスト用サーバー'}, 'テスト用サーバー'),
            ({'name': '長' * 101}, '長' * 100),
            ({}, ''), ({'name': None}, ''), ({'name': 123}, ''),
            ({'name': ['サーバー']}, ''), ({'name': {'name': 'サーバー'}}, ''),
        ):
            with self.subTest(guild_data=guild_data):
                self.configure_exchange(post, **guild_data)
                with self.assertLogs('community.views.bot_install', level='INFO') as logs:
                    self.assertRedirects(self.callback(self.begin()), self.settings_url)
                self.assertEqual(BotInstallation.objects.get().guild_name, expected)
                self.assertEqual(len(logs.output), 1)
                self.assertTrue(logs.output[0].endswith('bot_install result=installed'))
                BotInstallation.objects.all().delete()

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
        original = self.installation(guild_name='以前のサーバー名')
        self.configure_exchange(post, name='新しいサーバー名')
        response = self.callback(self.begin(self.second))
        self.assertContains(self.client.get(response.url), '既に別の集会に紐づいています')
        self.assertEqual(BotInstallation.objects.count(), 1)
        original.refresh_from_db()
        self.assertEqual(original.guild_name, '以前のサーバー名')
        self.client.post(reverse('community:bot_install_remove', args=[self.community.pk, original.pk]))
        original.refresh_from_db()
        self.assertEqual(original.status, BotInstallation.Status.REMOVED)
        self.assertIsNotNone(original.removed_at)
        self.assertRedirects(self.callback(self.begin(self.second)), self.settings_url)
        self.assertEqual(BotInstallation.objects.count(), 2)
        self.assertEqual(
            BotInstallation.objects.get(community=self.second, status='installed').guild_name, '新しいサーバー名',
        )

    @patch('community.views.bot_install.requests.post')
    def test_reauthorize_same_community_preserves_configured_status(self, post):
        original = self.installation(
            status='configured', configured_at=timezone.now(), guild_name='以前のサーバー名',
            notification_channel_id='223456789012345678',
        )
        configured_at = original.configured_at
        installed_at = original.installed_at
        self.configure_exchange(post, name='更新後のサーバー名')
        self.assertRedirects(self.callback(self.begin()), self.settings_url)
        original.refresh_from_db()
        self.assertEqual(original.status, 'configured')
        self.assertEqual(original.guild_name, '更新後のサーバー名')
        self.assertEqual(original.configured_at, configured_at)
        self.assertEqual(original.installed_at, installed_at)
        self.assertEqual(original.notification_channel_id, '223456789012345678')
        self.assertEqual(BotInstallation.objects.count(), 1)

    @patch('community.views.bot_install.requests.post')
    def test_reauthorize_waiting_installation_updates_and_normalizes_name(self, post):
        original = self.installation(guild_name='以前のサーバー名')
        # 名前が取れない再認証では、保存済みの名前を消さない。
        for guild_data, expected in (({'name': '新' * 101}, '新' * 100), ({'name': False}, '新' * 100), ({}, '新' * 100)):
            with self.subTest(guild_data=guild_data):
                self.configure_exchange(post, **guild_data)
                self.assertRedirects(self.callback(self.begin()), self.settings_url)
                original.refresh_from_db()
                self.assertEqual(original.guild_name, expected)
                self.assertEqual(original.status, 'installed')
                self.assertEqual(BotInstallation.objects.count(), 1)

    @patch('community.views.bot_install.requests.post')
    def test_readding_names_past_records_of_the_same_server(self, post):
        past = self.installation(status='removed', removed_at=timezone.now())
        self.configure_exchange(post, name='新しいサーバー名')
        self.assertRedirects(self.callback(self.begin()), self.settings_url)
        past.refresh_from_db()
        self.assertEqual(past.guild_name, '新しいサーバー名')
        self.assertEqual(BotInstallation.objects.get(status='installed').guild_name, '新しいサーバー名')

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

    def test_uninstalled_shows_purpose_and_primary_setup_action(self):
        section = self.bot_section()
        text = section.get_text(' ', strip=True)
        for expected in (
            'Discord Bot', '集会のお知らせを Discord で受け取るための Bot です。',
            'Bot はまだ追加されていません。',
            '追加後に、Discord でセットアップを行います。',
            '追加・連携解除はその場で反映されます。画面下の保存操作は不要です。',
            'Discord が開きます。追加先のサーバーを選んでください。',
        ):
            self.assertIn(expected, text)
        self.assertNotIn('現在、連携している Discord サーバーはありません。', text)
        form = section.select_one('form')
        self.assertEqual(form['action'], self.start_url)
        self.assertEqual(form['method'], 'post')
        self.assertIsNotNone(form.select_one('[name="csrfmiddlewaretoken"]'))
        self.assertEqual(form.button.get_text(strip=True), 'Discord に Bot を追加する')
        self.assertIn('setup-placeholder', form.button['class'])
        self.assertIsNone(section.select_one('details'))
        self.assertIsNone(section.select_one('[data-confirm-panel]'))

    def test_waiting_shows_name_steps_and_targeted_confirmation(self):
        record = self.installation(guild_name='準備中のサーバー')
        section = self.bot_section()
        self.assertEqual(section.strong.get_text(), record.guild_name)
        self.assertIn('セットアップ待ち', section.select_one('.detail-chip').get_text())
        self.assertIn('bg-warning-subtle', section.select_one('.detail-chip')['class'])
        self.assertIn('Bot の追加は完了しました。続いて Discord で設定してください。', section.get_text())
        self.assertEqual(section.select('ol li')[0].get_text(), 'Discord で『準備中のサーバー』サーバーを開く')
        self.assertEqual(
            section.select('ol li')[1].get_text(),
            'Bot が使えるテキストチャンネルのメッセージ入力欄に /hub setup を入力し、表示される案内に従う',
        )
        panel = section.select_one(f'#remove-bot-{record.pk}')
        self.assertTrue(panel.has_attr('hidden'))
        self.assertIn('『準備中のサーバー』との連携を解除しますか？', panel.get_text())
        self.assertIn(
            'この集会と Discord サーバーの連携を解除します。'
            'Discord サーバー内の Bot は削除されません。連携の記録は残ります。', panel.get_text(),
        )
        self.assertEqual(panel.form['action'], reverse(
            'community:bot_install_remove', args=[self.community.pk, record.pk],
        ))
        self.assertEqual(panel.select_one('.btn-danger').get_text(), '連携を解除する')
        self.assertNotIn(record.guild_id, str(section))

    def test_configured_shows_completion_and_secondary_add_button(self):
        record = self.installation(guild_name='稼働中のサーバー', status='configured')
        section = self.bot_section()
        self.assertEqual(section.strong.get_text(), record.guild_name)
        self.assertIn('稼働中', section.select_one('.detail-chip').get_text())
        self.assertIn('bg-success-subtle', section.select_one('.detail-chip')['class'])
        self.assertIn('このサーバーでのセットアップは完了しています。', section.get_text())
        self.assertNotIn('/hub setup', section.get_text())
        self.assertNotIn('通知は正常に届いています', section.get_text())
        self.assertNotIn(record.guild_id, str(section))
        button = section.select_one('[data-bot-install]')
        self.assertEqual(button.get_text(), '別のサーバーに Bot を追加する')
        self.assertIn('btn-outline-primary', button['class'])
        self.assertIn('btn-sm', button['class'])

    def test_history_only_shows_empty_current_state_and_closed_history(self):
        record = self.installation(guild_name='以前のサーバー', status='removed', removed_at=timezone.now())
        section = self.bot_section()
        history = section.select_one('details')
        self.assertFalse(history.has_attr('open'))
        self.assertEqual(history.summary.get_text(), '過去の連携記録（1件）')
        self.assertIn(record.guild_name, history.get_text())
        self.assertIn('連携終了日:', history.get_text())
        self.assertIn(timezone.localtime(record.removed_at).strftime('%Y年%-m月%-d日'), history.get_text())
        self.assertEqual(history.select_one('.detail-chip').get_text(), '連携終了')
        self.assertEqual(history.select_one('.detail-chip')['class'], ['detail-chip'])
        self.assertNotIn(record.guild_id, str(section))
        self.assertIsNone(section.select_one('[data-confirm-panel]'))
        history.decompose()
        self.assertNotIn(record.guild_name, section.get_text())
        self.assertIn('現在、連携している Discord サーバーはありません。', section.get_text())
        self.assertEqual(section.select_one('button[type="submit"]').get_text(), 'Discord に Bot を追加する')

    def test_current_and_history_are_separated_and_waiting_is_first(self):
        waiting = self.installation(guild_name='準備中のサーバー')
        configured = BotInstallation.objects.create(
            community=self.community, guild_id='223456789012345678', guild_name='稼働中のサーバー', status='configured',
        )
        # 同じサーバーの古い記録も、現在のカードと並べず履歴に入れる。
        self.installation(guild_name='前の連携記録', status='removed')
        BotInstallation.objects.create(
            community=self.community, guild_id='323456789012345678', guild_name='別の過去の記録', status='removed',
        )
        section = self.bot_section()
        self.assertEqual(
            [item.get_text() for item in section.select(':scope > div strong')],
            [waiting.guild_name, configured.guild_name],
        )
        history = section.select_one('details')
        self.assertFalse(history.has_attr('open'))
        self.assertEqual(history.summary.get_text(), '過去の連携記録（2件）')
        self.assertIn('前の連携記録', history.get_text())
        self.assertIn('別の過去の記録', history.get_text())
        self.assertNotIn('連携終了日:', history.get_text())
        self.assertIsNone(history.select_one('button'))
        history.decompose()
        self.assertNotIn('前の連携記録', section.get_text())
        self.assertNotIn('現在、連携している Discord サーバーはありません。', section.get_text())
        for guild_id in (waiting.guild_id, configured.guild_id, '323456789012345678'):
            self.assertNotIn(guild_id, str(section))

    def test_legacy_name_is_explained_and_id_is_only_in_details_or_confirmation(self):
        record = self.installation()
        section = self.bot_section()
        self.assertEqual(section.strong.get_text(), 'Discord サーバー（名前未登録）')
        self.assertIn('以前に追加されたため、サーバー名が保存されていません。', section.get_text())
        details = section.select_one('details')
        self.assertFalse(details.has_attr('open'))
        self.assertEqual(details.summary.get_text(), 'サーバーの詳細')
        self.assertIn(f'サーバー ID: {record.guild_id}', details.get_text())
        self.assertIn('追加日:', details.get_text())
        panel = section.select_one('.confirm-panel')
        self.assertIn(record.guild_id, panel.get_text())
        self.assertIn('Discord サーバー（名前未登録）', panel.get_text())
        details.decompose()
        panel.decompose()
        self.assertNotIn(record.guild_id, str(section))

    def test_legacy_history_has_name_fallback_and_closed_server_details(self):
        record = self.installation(status='removed')
        history = self.bot_section().select_one('details')
        self.assertEqual(history.strong.get_text(), 'Discord サーバー（名前未登録）')
        details = history.select_one('details')
        self.assertFalse(details.has_attr('open'))
        self.assertIn(record.guild_id, details.get_text())
        details.decompose()
        self.assertNotIn(record.guild_id, str(history))

    def test_guild_names_are_escaped_in_cards_steps_confirmation_and_history(self):
        unsafe = '<img src=x onerror=alert(1)>'
        for status in ('installed', 'configured', 'removed'):
            with self.subTest(status=status):
                record = self.installation(guild_name=unsafe, status=status)
                section = self.bot_section()
                self.assertEqual(section.strong.get_text(), unsafe)
                self.assertIsNone(section.select_one('img'))
                self.assertIsNone(section.select_one('[onerror]'))
                self.assertIn('&lt;img src=x onerror=alert(1)&gt;', str(section))
                record.delete()

    def test_read_only_section_never_renders_buttons_or_confirmation(self):
        self.installation(guild_name='表示用サーバー')
        self.installation(guild_name='過去のサーバー', status='removed')
        response = self.client.get(self.settings_url)
        context = response.context_data.copy()
        context['can_manage_bot'] = False
        section = BeautifulSoup(render_to_string('community/includes/bot_install.html', context), 'html.parser')
        self.assertIn('表示用サーバー', section.get_text())
        self.assertIn('過去のサーバー', section.get_text())
        self.assertIsNone(section.select_one('button, form, .confirm-panel, [data-confirm-panel]'))

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
