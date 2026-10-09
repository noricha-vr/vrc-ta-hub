"""運営スタッフ用画面（権限・作成・編集・取り消し・再送・一覧の並び）のテスト。"""
from __future__ import annotations

from datetime import timedelta, timezone as dt_timezone

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from announcement.models import DiscordScheduledMessage
from tests.factories import make_user

from ._helpers import jst, make_message

Status = DiscordScheduledMessage.Status


def _future(days: int = 3):
    """今から days 日後の 20:00（日本時間）。datetime-local の入力値と保存値の組を返す。"""
    target = (timezone.localtime(timezone.now()) + timedelta(days=days)).replace(
        hour=20, minute=0, second=0, microsecond=0,
    )
    return target.strftime('%Y-%m-%dT%H:%M'), target


class StaffAccessTests(TestCase):
    def setUp(self):
        self.message = make_message(scheduled_at=timezone.now() + timedelta(days=1))
        self.get_urls = [
            reverse('announcement:discord_list'),
            reverse('announcement:discord_create'),
            reverse('announcement:discord_detail', kwargs={'pk': self.message.pk}),
        ]
        self.post_urls = [
            reverse('announcement:discord_cancel', kwargs={'pk': self.message.pk}),
            reverse('announcement:discord_resend', kwargs={'pk': self.message.pk}),
        ]

    def test_anonymous_is_sent_to_login(self):
        for url in self.get_urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn('/account/login/', response.url)
        for url in self.post_urls:
            with self.subTest(url=url):
                self.assertIn('/account/login/', self.client.post(url).url)

    def test_non_staff_user_gets_403(self):
        self.client.force_login(make_user(user_name='member', email='member@example.com'))

        for url in self.get_urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        for url in self.post_urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url).status_code, 403)
        self.message.refresh_from_db()
        self.assertEqual(self.message.status, Status.SCHEDULED)

    def test_staff_and_superuser_can_open_pages(self):
        users = [
            make_user(user_name='staff', email='staff@example.com', is_staff=True),
            make_user(user_name='root', email='root@example.com', is_superuser=True),
        ]
        for user in users:
            self.client.force_login(user)
            for url in self.get_urls:
                with self.subTest(user=user.user_name, url=url):
                    self.assertEqual(self.client.get(url).status_code, 200)


class StaffTestCase(TestCase):
    def setUp(self):
        self.staff = make_user(user_name='staff', email='staff@example.com', is_staff=True)
        self.client.force_login(self.staff)

    def detail_url(self, message):
        return reverse('announcement:discord_detail', kwargs={'pk': message.pk})


class CreateViewTests(StaffTestCase):
    url = 'announcement:discord_create'

    def test_create_saves_jst_datetime_and_author(self):
        value, expected = _future()

        response = self.client.post(reverse(self.url), {'body': '今夜 21 時から発表会です', 'scheduled_at': value})

        message = DiscordScheduledMessage.objects.get()
        self.assertRedirects(response, self.detail_url(message))
        self.assertEqual(message.scheduled_at, expected)
        self.assertEqual(timezone.localtime(message.scheduled_at).hour, 20)
        self.assertEqual(message.scheduled_at.astimezone(dt_timezone.utc).hour, 11)
        self.assertEqual(message.status, Status.SCHEDULED)
        self.assertEqual(message.created_by, self.staff)
        self.assertFalse(message.mention_everyone_confirmed)

    def test_past_datetime_is_not_saved(self):
        past = (timezone.localtime(timezone.now()) - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M')

        response = self.client.post(reverse(self.url), {'body': '告知', 'scheduled_at': past})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '過去の日時は指定できません')
        self.assertFalse(DiscordScheduledMessage.objects.exists())

    def test_everyone_needs_confirmation_and_is_saved_as_confirmed(self):
        value, _ = _future()
        data = {'body': '@everyone 今夜です', 'scheduled_at': value}

        response = self.client.post(reverse(self.url), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(DiscordScheduledMessage.objects.exists())

        self.client.post(reverse(self.url), {**data, 'confirm_mass_mention': 'on'})
        self.assertTrue(DiscordScheduledMessage.objects.get().mention_everyone_confirmed)

    def test_create_page_explains_delay(self):
        response = self.client.get(reverse(self.url))

        self.assertContains(response, '最大 1 分ほど遅れて届く')
        self.assertContains(response, 'type="datetime-local"')


class EditViewTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.message = make_message(
            body='元の本文',
            scheduled_at=timezone.now() + timedelta(days=1),
            attempt_count=1,
            next_attempt_at=timezone.now() + timedelta(minutes=2),
            last_error='Discord が HTTP 503 を返しました。',
        )

    def test_scheduled_message_can_be_edited(self):
        value, expected = _future(days=5)

        response = self.client.post(self.detail_url(self.message), {'body': '新しい本文', 'scheduled_at': value})

        self.assertRedirects(response, self.detail_url(self.message))
        self.message.refresh_from_db()
        self.assertEqual(self.message.body, '新しい本文')
        self.assertEqual(self.message.scheduled_at, expected)
        self.assertEqual(self.message.attempt_count, 0)
        self.assertIsNone(self.message.next_attempt_at)
        self.assertEqual(self.message.last_error, '')

    def test_adding_everyone_on_edit_requires_confirmation(self):
        value, _ = _future()

        response = self.client.post(self.detail_url(self.message), {'body': '@everyone 追記', 'scheduled_at': value})

        self.assertEqual(response.status_code, 200)
        self.message.refresh_from_db()
        self.assertEqual(self.message.body, '元の本文')

    def test_confirmed_message_needs_confirmation_again_on_save(self):
        self.message.body = '@everyone 元の本文'
        self.message.mention_everyone_confirmed = True
        self.message.save()
        value, _ = _future()

        response = self.client.post(self.detail_url(self.message), {'body': '@everyone 直した本文', 'scheduled_at': value})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'チェックを入れてください')
        self.message.refresh_from_db()
        self.assertEqual(self.message.body, '@everyone 元の本文')

    def test_removing_everyone_clears_confirmation(self):
        self.message.body = '@everyone 元の本文'
        self.message.mention_everyone_confirmed = True
        self.message.save()
        value, _ = _future()

        self.client.post(self.detail_url(self.message), {'body': '全員宛てをやめた本文', 'scheduled_at': value})

        self.message.refresh_from_db()
        self.assertFalse(self.message.mention_everyone_confirmed)

    def test_sent_canceled_failed_and_sending_messages_cannot_be_edited(self):
        value, _ = _future()
        locked_states = [
            {'status': Status.SENT, 'sent_at': timezone.now()},
            {'status': Status.CANCELED},
            {'status': Status.FAILED},
            {'lease_token': 'running', 'lease_expires_at': timezone.now() + timedelta(minutes=5)},
        ]
        for fields in locked_states:
            with self.subTest(fields=fields):
                message = make_message(body='変えない本文', scheduled_at=timezone.now() + timedelta(days=1), **fields)

                response = self.client.post(self.detail_url(message), {'body': '書き換え', 'scheduled_at': value})

                self.assertRedirects(response, self.detail_url(message))
                message.refresh_from_db()
                self.assertEqual(message.body, '変えない本文')

    def test_edit_form_is_shown_only_while_editable(self):
        self.assertContains(self.client.get(self.detail_url(self.message)), 'data-testid="announce-edit-form"')

        sent = make_message(status=Status.SENT, sent_at=timezone.now(), discord_message_id='1300000000000000009')
        response = self.client.get(self.detail_url(sent))
        self.assertNotContains(response, 'data-testid="announce-edit-form"')
        self.assertNotContains(response, '予約を取り消す')
        self.assertContains(response, '1300000000000000009')


class CancelAndResendViewTests(StaffTestCase):
    def test_scheduled_message_can_be_canceled(self):
        message = make_message(scheduled_at=timezone.now() + timedelta(days=1))

        response = self.client.post(reverse('announcement:discord_cancel', kwargs={'pk': message.pk}))

        self.assertRedirects(response, self.detail_url(message))
        message.refresh_from_db()
        self.assertEqual(message.status, Status.CANCELED)

    def test_sent_or_sending_message_cannot_be_canceled(self):
        sent = make_message(status=Status.SENT, sent_at=timezone.now())
        sending = make_message(lease_token='running', lease_expires_at=timezone.now() + timedelta(minutes=5))

        for message in (sent, sending):
            with self.subTest(message=message.pk):
                self.client.post(reverse('announcement:discord_cancel', kwargs={'pk': message.pk}))
                message.refresh_from_db()
                self.assertNotEqual(message.status, Status.CANCELED)

    def test_failed_message_shows_error_and_can_be_resent(self):
        message = make_message(
            status=Status.FAILED, attempt_count=3, last_error='Discord が HTTP 503 を返しました。',
        )
        detail = self.client.get(self.detail_url(message))
        self.assertContains(detail, 'Discord が HTTP 503 を返しました。')
        self.assertContains(detail, '再送する')

        response = self.client.post(reverse('announcement:discord_resend', kwargs={'pk': message.pk}))

        self.assertRedirects(response, self.detail_url(message))
        message.refresh_from_db()
        self.assertEqual(message.status, Status.SCHEDULED)
        self.assertEqual(message.attempt_count, 0)
        self.assertEqual(message.last_error, '')

    def test_only_failed_message_can_be_resent(self):
        for status in (Status.SENT, Status.CANCELED, Status.SCHEDULED):
            with self.subTest(status=status):
                message = make_message(status=status)
                self.client.post(reverse('announcement:discord_resend', kwargs={'pk': message.pk}))
                message.refresh_from_db()
                self.assertEqual(message.status, status)
                self.assertEqual(message.attempt_count, 0)


class ListViewTests(StaffTestCase):
    def test_scheduled_messages_come_first_in_send_order(self):
        later = make_message(body='予約 後', scheduled_at=jst(2026, 12, 3, 20))
        sooner = make_message(body='予約 先', scheduled_at=jst(2026, 12, 2, 20))
        sent_new = make_message(body='送信済み 新', status=Status.SENT, scheduled_at=jst(2026, 11, 30, 20))
        failed_old = make_message(body='失敗 古', status=Status.FAILED, scheduled_at=jst(2026, 11, 1, 20))
        canceled = make_message(body='取り消し', status=Status.CANCELED, scheduled_at=jst(2026, 11, 15, 20))

        response = self.client.get(reverse('announcement:discord_list'))

        self.assertEqual(
            [message.pk for message in response.context['scheduled_messages']],
            [sooner.pk, later.pk, sent_new.pk, canceled.pk, failed_old.pk],
        )
        self.assertContains(response, '2026/12/02（水）20:00')
        self.assertContains(response, '予約中 2 件')
        self.assertContains(response, '失敗 1 件')

    def test_empty_state(self):
        response = self.client.get(reverse('announcement:discord_list'))

        self.assertContains(response, '予約はまだありません')
