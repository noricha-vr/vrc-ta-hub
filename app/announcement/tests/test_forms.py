"""予約フォーム（JST の入力・過去日時・文字数・全員への通知の選択）のテスト。"""
from __future__ import annotations

from datetime import timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django import forms
from django.test import TestCase
from django.utils import timezone

from announcement.forms import DiscordScheduledMessageForm
from announcement.forms import MASS_MENTION_BODY_ERROR
from announcement.models import DISCORD_CONTENT_MAX_LENGTH, DiscordScheduledMessage

from ._helpers import jst, make_message

NOW = jst(2026, 11, 1, 12, 0)


def _form(body='今夜の告知です', scheduled_at='2026-12-01T20:00', mention='none', now=NOW, instance=None):
    data = {'body': body, 'scheduled_at': scheduled_at, 'mention': mention}
    return DiscordScheduledMessageForm(data, now=now, instance=instance)


class ScheduledAtFieldTests(TestCase):
    def test_datetime_local_input_is_read_as_jst(self):
        form = _form(scheduled_at='2026-12-01T20:00')

        self.assertTrue(form.is_valid(), form.errors)
        scheduled_at = form.cleaned_data['scheduled_at']
        self.assertEqual(scheduled_at, jst(2026, 12, 1, 20, 0))
        self.assertEqual(scheduled_at.astimezone(dt_timezone.utc).hour, 11)

    def test_input_is_read_as_jst_even_if_another_timezone_is_active(self):
        with timezone.override(ZoneInfo('UTC')):
            form = _form(scheduled_at='2026-12-01T20:00')
            self.assertTrue(form.is_valid(), form.errors)
            rendered = str(DiscordScheduledMessageForm(instance=make_message(scheduled_at=jst(2026, 12, 1, 20)), now=NOW)['scheduled_at'])

        self.assertEqual(form.cleaned_data['scheduled_at'], jst(2026, 12, 1, 20, 0))
        self.assertIn('value="2026-12-01T20:00"', rendered)

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


class EditScheduledAtTests(TestCase):
    """編集では、日時を変えていない時だけ過ぎた日時でも通す（作成は今までどおり未来だけ）。"""

    def setUp(self):
        # NOW（11/1 12:00）より前の、時刻を過ぎた予約（再試行待ちなど）
        self.message = make_message(
            body='元の本文', scheduled_at=jst(2026, 10, 31, 20, 0), attempt_count=1,
        )

    def test_unchanged_past_time_is_accepted(self):
        form = _form(body='直した本文', scheduled_at='2026-10-31T20:00', instance=self.message)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['scheduled_at'], jst(2026, 10, 31, 20, 0))

    def test_unchanged_time_keeps_saved_seconds(self):
        saved = jst(2026, 10, 31, 20, 0) + timedelta(seconds=30)
        self.message.scheduled_at = saved
        self.message.save()

        form = _form(scheduled_at='2026-10-31T20:00', instance=self.message)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['scheduled_at'], saved)

    def test_changed_past_time_is_rejected(self):
        form = _form(scheduled_at='2026-10-31T21:00', instance=self.message)

        self.assertFalse(form.is_valid())
        self.assertIn('scheduled_at', form.errors)

    def test_changed_future_time_is_accepted(self):
        form = _form(scheduled_at='2026-11-02T20:00', instance=self.message)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['scheduled_at'], jst(2026, 11, 2, 20, 0))

    def test_past_message_form_has_no_min_attribute(self):
        """過ぎた日時のまま保存できるよう、ブラウザの min で止めない。"""
        rendered = str(DiscordScheduledMessageForm(instance=self.message, now=NOW)['scheduled_at'])

        self.assertIn('value="2026-10-31T20:00"', rendered)
        self.assertNotIn('min=', rendered)


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

    def test_characters_are_counted_by_code_point(self):
        """絵文字（UTF-16 では 2 単位）も 1 文字と数える。カウンタと同じ数え方。"""
        self.assertTrue(_form(body='😀' * DISCORD_CONTENT_MAX_LENGTH).is_valid())
        self.assertFalse(_form(body='😀' * (DISCORD_CONTENT_MAX_LENGTH + 1)).is_valid())

    def test_textarea_has_no_browser_maxlength(self):
        """ブラウザの maxlength は UTF-16 単位で効くので付けず、上限はカウンタ用の属性で渡す。"""
        rendered = str(DiscordScheduledMessageForm(now=NOW)['body'])

        self.assertNotIn('maxlength', rendered)
        self.assertIn(f'data-max-length="{DISCORD_CONTENT_MAX_LENGTH}"', rendered)


class MentionChoiceTests(TestCase):
    def test_each_choice_can_be_saved(self):
        for mention in DiscordScheduledMessage.Mention.values:
            with self.subTest(mention=mention):
                form = _form(mention=mention)
                self.assertTrue(form.is_valid(), form.errors)
                message = form.save()
                message.refresh_from_db()
                self.assertEqual(message.mention, mention)
                self.assertEqual(message.body, '今夜の告知です')

    def test_mentions_in_body_are_rejected_for_every_choice(self):
        for mention in DiscordScheduledMessage.Mention.values:
            for body in ('@everyone 今夜です', '今夜です @here', '引用 `@everyone`', '@hereafter'):
                with self.subTest(mention=mention, body=body):
                    form = _form(body=body, mention=mention)
                    self.assertFalse(form.is_valid())
                    self.assertIn(MASS_MENTION_BODY_ERROR, form.errors['body'])

    def test_roles_and_users_are_accepted(self):
        form = _form(body='<@&123> <@456> 今夜です')

        self.assertTrue(form.is_valid(), form.errors)

    def test_radio_widget_defaults_to_none_and_preserves_saved_choice(self):
        form = DiscordScheduledMessageForm(now=NOW)
        self.assertIsInstance(form.fields['mention'].widget, forms.RadioSelect)
        self.assertEqual(form['mention'].value(), 'none')
        self.assertNotIn('confirm_mass_mention', form.fields)
        for mention in DiscordScheduledMessage.Mention.values:
            with self.subTest(mention=mention):
                message = make_message(mention=mention)
                form = DiscordScheduledMessageForm(instance=message, now=NOW)
                self.assertEqual(form['mention'].value(), mention)

    def test_missing_or_invalid_choice_is_rejected(self):
        for mention in ('', 'invalid', '@everyone'):
            with self.subTest(mention=mention):
                form = _form(mention=mention)
                self.assertFalse(form.is_valid())
                self.assertIn('mention', form.errors)

    def test_limit_includes_mention_line(self):
        for mention, prefix in (('everyone', '@everyone\n'), ('here', '@here\n'), ('none', '')):
            for character in ('あ', '😀'):
                with self.subTest(mention=mention, character=character):
                    limit = DISCORD_CONTENT_MAX_LENGTH - len(prefix)
                    form = _form(body=character * limit, mention=mention)
                    self.assertTrue(form.is_valid(), form.errors)
                    form = _form(body=character * (limit + 1), mention=mention)
                    self.assertFalse(form.is_valid())
                    self.assertIn('body', form.errors)

    def test_limit_uses_normalized_body_with_mention(self):
        body = 'あ' * 1988 + '\r\nあ'
        form = _form(body=body, mention='everyone')

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(len('@everyone\n' + form.cleaned_data['body']), 2000)
        self.assertFalse(_form(body=body + 'あ', mention='everyone').is_valid())

    def test_save_does_not_change_legacy_confirmation(self):
        message = make_message(mention_everyone_confirmed=True)
        form = _form(mention='none', instance=message)

        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        message.refresh_from_db()
        self.assertTrue(message.mention_everyone_confirmed)
