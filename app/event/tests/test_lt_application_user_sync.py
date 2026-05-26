"""LT 申込フォーム経由で CustomUser.user_name と x_account が同期保存されるテスト."""
from datetime import date, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse

from community.models import Community
from event.models import EventDetail, Event
from event.tests.tweet_generation import TweetGenerationPatchMixin
from user_account.tests.utils import create_discord_linked_user

User = get_user_model()


class LTApplicationUserSyncTest(TweetGenerationPatchMixin, TestCase):
    """LT 申込フォーム送信時に user.user_name / x_account が更新されることを検証."""

    def setUp(self):
        self.client = Client()
        self.user = create_discord_linked_user(
            user_name='OriginalName',
            email='sync@example.com',
            password='testpass123',
        )

        self.community = Community.objects.create(
            name='SyncCommunity',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='owner',
            status='approved',
        )
        self.future_event = Event.objects.create(
            community=self.community,
            date=date.today() + timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon',
            accepts_lt_application=True,
        )
        self.url = reverse(
            'event:lt_application_create',
            kwargs={'community_pk': self.community.pk},
        )

    @patch('event.notifications.send_mail')
    def test_speaker_updates_user_name(self, mock_send):
        """speaker を変更して送信すると user.user_name が更新される."""
        mock_send.return_value = 1
        self.client.login(username='OriginalName', password='testpass123')
        response = self.client.post(self.url, {
            'event': self.future_event.pk,
            'theme': 'X-sync',
            'speaker': 'NewName',
            'duration': 15,
            'x_account': 'noricha_vr',
        })
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.user_name, 'NewName')
        self.assertEqual(self.user.x_account, 'noricha_vr')

        ed = EventDetail.objects.get(event=self.future_event, theme='X-sync')
        self.assertEqual(ed.speaker, 'NewName')

    @patch('event.notifications.send_mail')
    def test_x_account_normalized_from_url(self, mock_send):
        """x_account に URL を送ると正規化される."""
        mock_send.return_value = 1
        self.client.login(username='OriginalName', password='testpass123')
        response = self.client.post(self.url, {
            'event': self.future_event.pk,
            'theme': 'normalize',
            'speaker': 'OriginalName',
            'duration': 15,
            'x_account': 'https://x.com/noricha_vr',
        })
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.x_account, 'noricha_vr')

    @patch('event.notifications.send_mail')
    def test_x_account_empty_is_allowed(self, mock_send):
        """x_account 未入力でも申込が成立する."""
        mock_send.return_value = 1
        self.client.login(username='OriginalName', password='testpass123')
        response = self.client.post(self.url, {
            'event': self.future_event.pk,
            'theme': 'empty-x',
            'speaker': 'OriginalName',
            'duration': 15,
            'x_account': '',
        })
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.x_account, '')

    @patch('event.notifications.send_mail')
    def test_speaker_duplicate_other_user_rejected(self, mock_send):
        """他ユーザーが同じ user_name を持つ場合はエラーで申込不可."""
        mock_send.return_value = 1
        User.objects.create_user(
            user_name='TakenName',
            email='other@example.com',
            password='pw',
        )
        self.client.login(username='OriginalName', password='testpass123')
        response = self.client.post(self.url, {
            'event': self.future_event.pk,
            'theme': 'dup',
            'speaker': 'TakenName',
            'duration': 15,
        })
        # フォームエラーで再描画される
        self.assertEqual(response.status_code, 200)
        self.assertFalse(EventDetail.objects.filter(theme='dup').exists())
        self.user.refresh_from_db()
        self.assertEqual(self.user.user_name, 'OriginalName')

    @patch('event.notifications.send_mail')
    def test_speaker_same_as_self_is_allowed(self, mock_send):
        """自分自身の user_name と同じ speaker は許容される."""
        mock_send.return_value = 1
        self.client.login(username='OriginalName', password='testpass123')
        response = self.client.post(self.url, {
            'event': self.future_event.pk,
            'theme': 'same',
            'speaker': 'OriginalName',
            'duration': 15,
        })
        self.assertEqual(response.status_code, 302)

    @patch('event.notifications.send_mail')
    def test_speaker_invalid_chars_rejected(self, mock_send):
        """不正な文字（空白）を含む speaker はバリデーションエラー."""
        mock_send.return_value = 1
        self.client.login(username='OriginalName', password='testpass123')
        response = self.client.post(self.url, {
            'event': self.future_event.pk,
            'theme': 'bad',
            'speaker': 'has space',
            'duration': 15,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(EventDetail.objects.filter(theme='bad').exists())
        self.user.refresh_from_db()
        self.assertEqual(self.user.user_name, 'OriginalName')

    def test_form_initial_includes_x_account(self):
        """GET 時に x_account の初期値が user の値で埋まる."""
        self.user.x_account = 'initial_handle'
        self.user.save(update_fields=['x_account'])
        self.client.login(username='OriginalName', password='testpass123')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertEqual(form.fields['x_account'].initial, 'initial_handle')
        self.assertEqual(form.fields['speaker'].initial, 'OriginalName')
