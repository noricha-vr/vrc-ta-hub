"""予約フォーム（JST の入力・過去日時・文字数・全員宛てメンションの確認）のテスト。"""
from __future__ import annotations

from datetime import timedelta, timezone as dt_timezone

from django.test import TestCase

from announcement.forms import DiscordScheduledMessageForm
from announcement.models import DISCORD_CONTENT_MAX_LENGTH

from ._helpers import jst, make_message

NOW = jst(2026, 11, 1, 12, 0)


def _form(body='今夜の告知です', scheduled_at='2026-12-01T20:00', confirm=False, now=NOW, instance=None):
    data = {'body': body, 'scheduled_at': scheduled_at}
    if confirm:
        data['confirm_mass_mention'] = 'on'
    return DiscordScheduledMessageForm(data, now=now, instance=instance)


class ScheduledAtFieldTests(TestCase):
    def test_datetime_local_input_is_read_as_jst(self):
        form = _form(scheduled_at='2026-12-01T20:00')

        self.assertTrue(form.is_valid(), form.errors)
        scheduled_at = form.cleaned_data['scheduled_at']
        self.assertEqual(scheduled_at, jst(2026, 12, 1, 20, 0))
        self.assertEqual(scheduled_at.astimezone(dt_timezone.utc).hour, 11)

    def test_saved_value_is_shown_in_jst(self):
        message = make_message(scheduled_at=jst(2026, 12, 1, 20, 0).astimezone(dt_timezone.utc))

        rendered = str(DiscordScheduledMessageForm(instance=message, now=NOW)['scheduled_at'])

        self.assertIn('type="datetime-local"', rendered)
        self.assertIn('value="2026-12-01T20:00"', rendered)
        self.assertIn('min="2026-11-01T12:00"', rendered)

    def test_past_datetime_is_rejected(self):
        now = jst(2026, 12, 1, 20, 0) + timedelta(seconds=30)

        for value in ('2026-12-01T19:59', '2026-12-01T20:00', '2025-12-01T21:00'):
            with self.subTest(value=value):
                form = _form(scheduled_at=value, now=now)
                self.assertFalse(form.is_valid())
                self.assertIn('scheduled_at', form.errors)

        self.assertTrue(_form(scheduled_at='2026-12-01T20:01', now=now).is_valid())

    def test_invalid_datetime_is_rejected(self):
        form = _form(scheduled_at='not-a-date')

        self.assertFalse(form.is_valid())
        self.assertIn('scheduled_at', form.errors)


class BodyFieldTests(TestCase):
    def test_body_up_to_discord_limit_is_accepted(self):
        self.assertTrue(_form(body='あ' * DISCORD_CONTENT_MAX_LENGTH).is_valid())

        form = _form(body='あ' * (DISCORD_CONTENT_MAX_LENGTH + 1))
        self.assertFalse(form.is_valid())
        self.assertIn('body', form.errors)

    def test_crlf_from_browser_counts_as_one_character(self):
        body = '\r\n'.join(['あ' * 99] * 20)  # 改行を 1 文字で数えると 1999 文字

        form = _form(body=body)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn('\r', form.cleaned_data['body'])
        self.assertEqual(len(form.cleaned_data['body']), 1999)

    def test_empty_body_is_rejected(self):
        self.assertFalse(_form(body='   ').is_valid())


class MassMentionConfirmationTests(TestCase):
    def test_everyone_and_here_require_confirmation(self):
        for body in ('@everyone 今夜です', '今夜です @here'):
            with self.subTest(body=body):
                form = _form(body=body)
                self.assertFalse(form.is_valid())
                self.assertIn('confirm_mass_mention', form.errors)

    def test_confirmed_mass_mention_is_saved_as_confirmed(self):
        form = _form(body='@everyone 今夜です', confirm=True)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.mention_everyone_confirmed)

    def test_checkbox_without_mass_mention_does_not_confirm(self):
        form = _form(body='<@&123> 今夜です', confirm=True)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.mention_everyone_confirmed)

    def test_confirmation_checkbox_is_never_prefilled(self):
        """保存済みの確認を引き継がず、保存のたびにチェックしてもらう。"""
        message = make_message(body='@everyone 今夜です', mention_everyone_confirmed=True)

        form = DiscordScheduledMessageForm(instance=message, now=NOW)

        self.assertNotIn('checked', str(form['confirm_mass_mention']))
        self.assertTrue(form.shows_mass_mention_confirm)
