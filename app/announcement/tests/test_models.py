"""予約のモデル（編集できるかの判定が QuerySet と property で一致すること）のテスト。"""
from __future__ import annotations

from datetime import timedelta

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from announcement.models import DiscordScheduledMessage

from ._helpers import make_message

Status = DiscordScheduledMessage.Status


class EditableDefinitionTests(TestCase):
    def setUp(self):
        lease_expires_at = timezone.now() + timedelta(minutes=5)
        self.messages = {
            'scheduled': make_message(),
            'waiting_retry': make_message(attempt_count=1, next_attempt_at=timezone.now() + timedelta(minutes=2)),
            'sending': make_message(lease_token='running', lease_expires_at=lease_expires_at),
            'sent': make_message(status=Status.SENT, sent_at=timezone.now()),
            'failed': make_message(status=Status.FAILED),
            'canceled': make_message(status=Status.CANCELED),
        }

    def test_is_editable_matches_editable_queryset(self):
        editable_ids = set(DiscordScheduledMessage.objects.editable().values_list('pk', flat=True))

        for name, message in self.messages.items():
            with self.subTest(name=name):
                self.assertEqual(message.is_editable, message.pk in editable_ids)
        self.assertEqual(editable_ids, {self.messages['scheduled'].pk, self.messages['waiting_retry'].pk})

    def test_is_sending_is_scheduled_but_not_editable(self):
        for name, message in self.messages.items():
            with self.subTest(name=name):
                self.assertEqual(message.is_sending, name == 'sending')


class MentionFieldTests(TestCase):
    def test_default_is_none(self):
        message = make_message()

        message.refresh_from_db()
        self.assertEqual(message.mention, 'none')

    def test_choice_labels(self):
        self.assertEqual(DiscordScheduledMessage.Mention.choices, [
            ('everyone', '@everyone（全員）'), ('here', '@here（オンラインの人）'), ('none', 'なし'),
        ])


class MentionMigrationTests(TransactionTestCase):
    """旧列を保って選択値を移し、移行後も旧モデルから予約を作れることを確かめる。"""

    migrate_from = [('announcement', '0001_initial')]
    migrate_to = [('announcement', '0002_discordscheduledmessage_mention')]

    def setUp(self):
        executor = MigrationExecutor(connection)
        self.addCleanup(self._restore_latest_schema)
        executor.migrate(self.migrate_from)
        self.old_model = executor.loader.project_state(self.migrate_from).apps.get_model(
            'announcement', 'DiscordScheduledMessage',
        )

    def _restore_latest_schema(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_confirmed_rows_are_migrated_and_legacy_column_is_preserved(self):
        confirmed = self.old_model.objects.create(
            body='@everyone 元の本文', scheduled_at=timezone.now(), mention_everyone_confirmed=True,
        )
        unconfirmed = self.old_model.objects.create(body='通知なしの本文', scheduled_at=timezone.now())

        MigrationExecutor(connection).migrate(self.migrate_to)

        migrated = DiscordScheduledMessage.objects.get(pk=confirmed.pk)
        self.assertEqual(migrated.mention, 'everyone')
        self.assertTrue(migrated.mention_everyone_confirmed)
        self.assertEqual(migrated.body, confirmed.body)
        self.assertEqual(DiscordScheduledMessage.objects.get(pk=unconfirmed.pk).mention, 'none')
        # 移行後も旧リビジョンが読み書きする列は残っている
        self.assertTrue(self.old_model.objects.get(pk=confirmed.pk).mention_everyone_confirmed)
        pk = self.old_model.objects.create(body='旧モデルからの予約', scheduled_at=timezone.now()).pk
        self.assertEqual(DiscordScheduledMessage.objects.get(pk=pk).mention, 'none')

    def test_reverse_leaves_legacy_data_unchanged(self):
        old = self.old_model.objects.create(
            body='元の本文', scheduled_at=timezone.now(), mention_everyone_confirmed=True,
        )
        MigrationExecutor(connection).migrate(self.migrate_to)
        DiscordScheduledMessage.objects.filter(pk=old.pk).update(mention='here')

        MigrationExecutor(connection).migrate(self.migrate_from)

        restored = self.old_model.objects.get(pk=old.pk)
        self.assertTrue(restored.mention_everyone_confirmed)
        self.assertEqual(restored.body, old.body)
