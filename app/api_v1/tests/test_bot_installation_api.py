"""Bot 導入 API の認証・入力・状態遷移を合成データで検証する。"""

import json
from datetime import timedelta

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from community.models import BotInstallation
from tests.factories import make_community, make_discord_linked_user
from user_account.models import APIKey


class BotInstallationAPITest(TestCase):
    guild_id = '123456789012345678'

    @classmethod
    def setUpTestData(cls):
        cls.admin = make_discord_linked_user(
            user_name='bot_api_admin', email='bot_api_admin@example.com',
            is_superuser=True, is_staff=True,
        )
        cls.community = make_community(name='テスト集会')
        cls.key, cls.raw_key = APIKey.create_with_raw_key(user=cls.admin, name='Bot 状態更新テスト')

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client = APIClient()
        self.client.credentials(HTTP_X_API_KEY=self.raw_key)
        self.url = reverse('bot-installation')
        self.status_url = reverse('bot-installation-status')
        self.installation = BotInstallation.objects.create(
            community=self.community, guild_id=self.guild_id, added_by=self.admin,
        )

    def payload(self, status='installed'):
        return {'id': self.installation.pk, 'community_id': self.community.pk, 'status': status,
                'guild_id': self.guild_id}

    def post_status(self, status='configured', **overrides):
        return self.client.post(
            self.status_url,
            {'installation_id': self.installation.pk, 'guild_id': self.guild_id, 'status': status, **overrides},
            format='json',
        )

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.json()['code'], code)
        self.assertTrue(response.json()['detail'])

    def assert_installed(self):
        self.installation.refresh_from_db()
        self.assertEqual(self.installation.status, 'installed')
        self.assertIsNone(self.installation.configured_at)
        self.assertIsNone(self.installation.removed_at)

    def test_get_returns_only_public_installation_fields(self):
        self.assertEqual(self.url, '/api/v1/bot-installation/')
        response = self.client.get(self.url, {'guild_id': self.guild_id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.payload())
        self.assert_installed()

    def test_get_configured_installation(self):
        self.installation.status = 'configured'
        self.installation.configured_at = timezone.now()
        self.installation.save()
        response = self.client.get(self.url, {'guild_id': self.guild_id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.payload('configured'))

    def test_missing_or_removed_installation_is_404(self):
        for guild_id in ('987654321098765432', self.guild_id):
            with self.subTest(guild_id=guild_id):
                self.installation.status = 'removed'
                self.installation.removed_at = timezone.now()
                self.installation.save()
                self.assert_error(self.client.get(self.url, {'guild_id': guild_id}), 404, 'error')
                for status in ('configured', 'removed'):
                    self.assert_error(self.post_status(status, guild_id=guild_id), 404, 'error')
                self.installation.refresh_from_db()
                self.assertEqual(self.installation.status, 'removed')
                self.assertIsNone(self.installation.configured_at)

    def test_removed_history_is_untouched_when_new_installation_exists(self):
        removed_at = timezone.now() - timedelta(days=1)
        historical = BotInstallation.objects.create(
            community=make_community(name='履歴用テスト集会'), guild_id=self.guild_id,
            added_by=self.admin, status='removed', removed_at=removed_at,
        )
        self.assertEqual(self.client.get(self.url, {'guild_id': self.guild_id}).json(), self.payload())
        self.assertEqual(self.post_status().json(), self.payload('configured'))
        historical.refresh_from_db()
        self.assertEqual(historical.status, 'removed')
        self.assertEqual(historical.removed_at, removed_at)
        self.assertIsNone(historical.configured_at)

    def test_guild_id_accepts_only_17_to_20_ascii_digits(self):
        for length in range(17, 21):
            with self.subTest(length=length):
                guild_id = '9' * length
                record = BotInstallation.objects.create(
                    community=self.community, guild_id=guild_id, added_by=self.admin,
                )
                expected = {**self.payload(), 'id': record.pk, 'guild_id': guild_id}
                response = self.client.get(self.url, {'guild_id': guild_id})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), expected)
                response = self.post_status(guild_id=guild_id, installation_id=record.pk)
                self.assertEqual(response.status_code, 200)
                record.refresh_from_db()
                self.assertEqual(record.status, 'configured')

    def test_invalid_and_missing_guild_id_are_400_for_both_endpoints(self):
        invalid_ids = [
            '', '1' * 16, '1' * 21, 'a' * 18, '12345678901234567a',
            '１' * 18, '١' * 18, f' {self.guild_id}', f'{self.guild_id} ',
            f'{self.guild_id}\n', f'+{self.guild_id}', f'-{self.guild_id}',
        ]
        for guild_id in invalid_ids:
            with self.subTest(guild_id=guild_id):
                self.assert_error(self.client.get(self.url, {'guild_id': guild_id}), 400, 'validation_error')
                self.assert_error(self.post_status(guild_id=guild_id), 400, 'validation_error')
        self.assert_error(self.client.get(self.url), 400, 'validation_error')
        self.assert_error(self.client.post(self.status_url, {'status': 'configured'}, format='json'),
                          400, 'validation_error')
        self.assert_installed()

    def test_non_string_guild_id_is_400(self):
        for guild_id in (None, int(self.guild_id), True, [], {}):
            with self.subTest(guild_id=guild_id):
                self.assert_error(self.post_status(guild_id=guild_id), 400, 'validation_error')
        self.assert_installed()

    def test_invalid_or_missing_installation_id_is_400(self):
        for installation_id in (None, 0, -1, True, str(self.installation.pk), 1.5, [], {}):
            with self.subTest(installation_id=installation_id):
                self.assert_error(self.post_status(installation_id=installation_id), 400, 'validation_error')
        self.assert_error(self.client.post(
            self.status_url, {'guild_id': self.guild_id, 'status': 'configured'}, format='json',
        ), 400, 'validation_error')
        self.assert_installed()

    def test_stale_notice_for_old_installation_does_not_touch_reinstalled_record(self):
        old_id = self.installation.pk
        BotInstallation.objects.filter(pk=old_id).update(status='removed', removed_at=timezone.now())
        new = BotInstallation.objects.create(
            community=make_community(name='別の集会'), guild_id=self.guild_id, added_by=self.admin,
        )
        for target in ('configured', 'removed'):
            with self.subTest(target=target):
                self.assert_error(self.post_status(target, installation_id=old_id), 404, 'error')
                new.refresh_from_db()
                self.assertEqual(new.status, 'installed')
                self.assertIsNone(new.configured_at)
                self.assertIsNone(new.removed_at)

    def test_installation_id_of_other_guild_is_404(self):
        other = BotInstallation.objects.create(
            community=make_community(name='別サーバーの集会'), guild_id='223456789012345678', added_by=self.admin,
        )
        self.assert_error(self.post_status(installation_id=other.pk), 404, 'error')
        other.refresh_from_db()
        self.assertEqual(other.status, 'installed')
        self.assert_installed()

    def test_configure_records_timestamp_and_is_idempotent(self):
        self.assertEqual(self.status_url, '/api/v1/bot-installation/status/')
        before = timezone.now()
        response = self.post_status()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.payload('configured'))
        self.installation.refresh_from_db()
        configured_at = self.installation.configured_at
        self.assertLessEqual(before, configured_at)
        self.assertLessEqual(configured_at, timezone.now())
        installed_at = self.installation.installed_at
        # 過去の設定時刻を使い、再送で now に書き換わらないことを確かめる。
        configured_at -= timedelta(days=1)
        BotInstallation.objects.filter(pk=self.installation.pk).update(configured_at=configured_at)
        response = self.post_status()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.payload('configured'))
        self.installation.refresh_from_db()
        self.assertEqual(self.installation.configured_at, configured_at)
        self.assertEqual(self.installation.installed_at, installed_at)
        self.assertIsNone(self.installation.removed_at)

    def test_remove_from_installed_or_configured_and_never_update_removed_record(self):
        for initial in ('installed', 'configured'):
            with self.subTest(initial=initial):
                configured_at = timezone.now() - timedelta(days=1) if initial == 'configured' else None
                BotInstallation.objects.filter(pk=self.installation.pk).update(
                    status=initial, configured_at=configured_at, removed_at=None,
                )
                before = timezone.now()
                response = self.post_status('removed')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), self.payload('removed'))
                self.installation.refresh_from_db()
                removed_at = self.installation.removed_at
                self.assertLessEqual(before, removed_at)
                self.assertLessEqual(removed_at, timezone.now())
                self.assertEqual(self.installation.configured_at, configured_at)
                for target in ('configured', 'removed'):
                    self.assert_error(self.post_status(target), 404, 'error')
                self.installation.refresh_from_db()
                self.assertEqual(self.installation.status, 'removed')
                self.assertEqual(self.installation.removed_at, removed_at)
                self.assertEqual(self.installation.configured_at, configured_at)

    def test_invalid_and_missing_status_are_400_without_changes(self):
        for initial in ('installed', 'configured'):
            BotInstallation.objects.filter(pk=self.installation.pk).update(status=initial)
            for target in ('installed', 'unknown', 'CONFIGURED', '', None, True, [], {}):
                with self.subTest(initial=initial, target=target):
                    self.assert_error(self.post_status(target), 400, 'validation_error')
                    self.installation.refresh_from_db()
                    self.assertEqual(self.installation.status, initial)
            self.assert_error(self.client.post(
                self.status_url, {'installation_id': self.installation.pk, 'guild_id': self.guild_id}, format='json',
            ), 400, 'validation_error')

    def test_non_object_or_malformed_json_is_400(self):
        for body in ([], 'configured', 123, None):
            with self.subTest(body=body):
                response = self.client.post(self.status_url, json.dumps(body), content_type='application/json')
                self.assert_error(response, 400, 'validation_error')
        self.assert_error(self.client.post(self.status_url, '{', content_type='application/json'), 400, 'parse_error')
        self.assert_installed()

    def test_form_submission_is_not_supported(self):
        response = self.client.post(self.status_url, {'guild_id': self.guild_id, 'status': 'configured'})
        self.assert_error(response, 415, 'unsupported_media_type')
        self.assert_installed()

    def test_anonymous_invalid_inactive_and_non_superuser_keys_are_denied(self):
        user = make_discord_linked_user(user_name='bot_api_staff', email='bot_api_staff@example.com', is_staff=True)
        _, staff_key = APIKey.create_with_raw_key(user=user, name='権限テスト')
        inactive_key, inactive_raw = APIKey.create_with_raw_key(user=self.admin, name='無効キーテスト')
        inactive_key.is_active = False
        inactive_key.save(update_fields=['is_active'])
        for raw_key in ('', 'invalid-key', inactive_raw, staff_key):
            with self.subTest(raw_key_kind='absent' if not raw_key else 'denied'):
                self.client.credentials(HTTP_X_API_KEY=raw_key)
                expected_code = ('not_authenticated' if not raw_key else
                                 'permission_denied' if raw_key == staff_key else 'invalid_api_key')
                self.assert_error(self.client.get(self.url, {'guild_id': self.guild_id}), 403, expected_code)
                self.assert_error(self.post_status(), 403, expected_code)
        self.assert_installed()

    def test_session_alone_does_not_grant_access(self):
        self.client.credentials()
        self.client.force_login(self.admin)
        self.assert_error(self.client.get(self.url, {'guild_id': self.guild_id}), 403, 'not_authenticated')
        self.assert_error(self.post_status(), 403, 'not_authenticated')
        self.assert_installed()

    def test_read_only_key_can_read_but_cannot_update(self):
        self.key.scope = APIKey.SCOPE_READ
        self.key.save(update_fields=['scope'])
        self.assertEqual(self.client.get(self.url, {'guild_id': self.guild_id}).status_code, 200)
        for target in ('configured', 'removed'):
            self.assert_error(self.post_status(target), 403, 'read_only_api_key')
        self.assert_installed()

    def test_authentication_precedes_validation(self):
        self.client.credentials()
        self.assert_error(self.client.get(self.url, {'guild_id': 'invalid'}), 403, 'not_authenticated')
        self.assert_error(self.post_status('installed', guild_id='invalid'), 403, 'not_authenticated')

    def test_other_http_methods_are_not_allowed(self):
        for method in ('post', 'put', 'patch', 'delete'):
            self.assert_error(getattr(self.client, method)(self.url, {}, format='json'), 405, 'method_not_allowed')
        for method in ('get', 'put', 'patch', 'delete'):
            self.assert_error(getattr(self.client, method)(self.status_url, {}, format='json'), 405, 'method_not_allowed')
        self.assert_installed()


class BotInstallationLockTest(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_status_read_and_write_take_row_lock_inside_transaction(self):
        cache.clear()
        self.addCleanup(cache.clear)
        admin = make_discord_linked_user(
            user_name='bot_lock_admin', email='bot_lock_admin@example.com', is_superuser=True,
        )
        _, raw_key = APIKey.create_with_raw_key(user=admin, name='行ロックテスト')
        record = BotInstallation.objects.create(
            community=make_community(), guild_id='123456789012345678', added_by=admin,
        )
        client = APIClient()
        client.credentials(HTTP_X_API_KEY=raw_key)
        for target in ('configured', 'removed'):
            with self.subTest(target=target):
                locking_reads = []
                writes = []

                def inspect_sql(execute, sql, params, many, context):
                    if 'community_botinstallation' in sql:
                        if 'FOR UPDATE' in sql.upper():
                            locking_reads.append(connection.in_atomic_block)
                        if sql.upper().startswith('UPDATE'):
                            self.assertTrue(locking_reads, '書き込み前に行をロックする')
                            writes.append(connection.in_atomic_block)
                    return execute(sql, params, many, context)

                with connection.execute_wrapper(inspect_sql):
                    response = client.post(reverse('bot-installation-status'),
                                           {'installation_id': record.pk, 'guild_id': record.guild_id,
                                            'status': target}, format='json')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(locking_reads, [True])
                self.assertEqual(writes, [True])
                record.refresh_from_db()
                self.assertEqual(record.status, target)
