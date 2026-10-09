"""予約のモデル（編集できるかの判定が QuerySet と property で一致すること）のテスト。"""
from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
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
