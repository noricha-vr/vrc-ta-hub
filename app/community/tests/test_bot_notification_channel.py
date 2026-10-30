"""Discord Bot の通知先の保存・検証と旧コードの INSERT 互換性。"""

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from community.models import BotInstallation
from tests.factories import make_community


class BotNotificationChannelTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.community = make_community()

    def test_channel_round_trip_and_nullable_default(self):
        record = BotInstallation.objects.create(community=self.community, guild_id='1' * 18, added_by=None)
        self.assertIsNone(record.notification_channel_id)
        record.refresh_from_db()
        self.assertIsNone(record.notification_channel_id)
        for value in ('7' * length for length in range(17, 21)):
            record.notification_channel_id = value
            record.full_clean(exclude=['added_by'])
            record.save()
            record.refresh_from_db()
            self.assertEqual(record.notification_channel_id, value)

    def test_model_rejects_invalid_channel_ids(self):
        record = BotInstallation(community=self.community, guild_id='1' * 18, added_by=None)
        for value in ('1' * 16, '1' * 21, 'x' * 18, '１' * 18, '١' * 18,
                      ' ' + '1' * 18, '1' * 18 + ' ', '1' * 18 + '\n'):
            with self.subTest(value=value), self.assertRaises(ValidationError) as error:
                record.notification_channel_id = value
                record.clean_fields(exclude=['added_by'])
            self.assertIn('notification_channel_id', error.exception.message_dict)
        for value in (None, ''):
            record.notification_channel_id = value
            record.clean_fields(exclude=['added_by'])

    def test_old_insert_without_channel_uses_null(self):
        # 旧リビジョンのように、新しい列を指定しない INSERT を実行する。
        with connection.cursor() as cursor:
            cursor.execute(
                'INSERT INTO community_botinstallation '
                '(community_id, guild_id, status, added_by_id, installed_at, configured_at, removed_at) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s)',
                [self.community.pk, '1' * 18, 'installed', None, timezone.now(), None, None],
            )
        record = BotInstallation.objects.get(guild_id='1' * 18)
        self.assertIsNone(record.notification_channel_id)
        self.assertEqual(record.active_guild_id, record.guild_id)
