"""予約操作の権限、日時、送信開始との競合、定期処理の認証を検証する。"""

from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models.query import QuerySet
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from tests.factories import make_discord_linked_user

from discord_scheduler.forms import ScheduledDiscordPostForm
from discord_scheduler.models import ScheduledDiscordPost


JST = ZoneInfo('Asia/Tokyo')
CHANNEL_URL = 'https://discord.com/channels/1143765879377645628/1304472925058891899'
WEBHOOK_URL = 'https://discord.com/api/webhooks/123456789012345678/test-webhook-token'


class ScheduledPostFormTests(SimpleTestCase):
    def test_datetime_input_is_jst_even_when_current_timezone_is_utc(self):
        with timezone.override('UTC'):
            form = ScheduledDiscordPostForm(data={
                'content': '開催のお知らせ',
                'scheduled_at': '2099-07-01T20:00',
            })
            self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data['scheduled_at'],
            datetime(2099, 7, 1, 11, 0, tzinfo=datetime_timezone.utc),
        )

    def test_edit_initial_time_is_rendered_in_jst(self):
        post = ScheduledDiscordPost(
            content='開催のお知らせ',
            scheduled_at=datetime(2099, 7, 1, 11, 0, tzinfo=datetime_timezone.utc),
        )
        with timezone.override('America/Los_Angeles'):
            form = ScheduledDiscordPostForm(instance=post)
            self.assertIn('value="2099-07-01T20:00"', str(form['scheduled_at']))

    def test_blank_and_oversized_content_is_rejected(self):
        for content in (' \n\t', 'あ' * 2001, '😀' * 1001):
            with self.subTest(content_length=len(content)):
                form = ScheduledDiscordPostForm(data={
                    'content': content,
                    'scheduled_at': '2099-07-01T20:00',
                })
                self.assertFalse(form.is_valid())
                self.assertIn('content', form.errors)

    def test_past_time_is_rejected(self):
        form = ScheduledDiscordPostForm(data={
            'content': '開催のお知らせ',
            'scheduled_at': '2000-01-01T20:00',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('scheduled_at', form.errors)

    def test_markdown_whitespace_is_preserved(self):
        content = '    インデント付きの本文\n\n'
        form = ScheduledDiscordPostForm(data={'content': content, 'scheduled_at': '2099-07-01T20:00'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['content'], content)


@override_settings(
    DISCORD_SCHEDULED_WEBHOOK_URL=WEBHOOK_URL,
    DISCORD_SCHEDULED_CHANNEL_URL=CHANNEL_URL,
)
class ScheduledPostViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = make_discord_linked_user(
            user_name='discord_scheduler_staff', email='scheduler-staff@example.com', is_staff=True,
        )
        cls.regular_user = make_discord_linked_user(
            user_name='discord_scheduler_regular', email='scheduler-regular@example.com',
        )

    def setUp(self):
        self.client.force_login(self.staff)
        self.future = timezone.now() + timedelta(days=3)

    def make_post(self, **overrides):
        data = {'content': '開催のお知らせ', 'scheduled_at': self.future, 'created_by': self.staff}
        data.update(overrides)
        return ScheduledDiscordPost.objects.create(**data)

    def form_data(self, **overrides):
        data = {
            'content': '更新したお知らせ\nhttps://vrc-ta-hub.com/',
            'scheduled_at': timezone.localtime(self.future, JST).strftime('%Y-%m-%dT%H:%M'),
        }
        data.update(overrides)
        return data

    def urls_for_post(self, post):
        return [
            reverse('discord_scheduler:post_list'),
            reverse('discord_scheduler:post_create'),
            reverse('discord_scheduler:post_edit', args=[post.pk]),
            reverse('discord_scheduler:post_cancel', args=[post.pk]),
        ]

    def test_anonymous_visitors_are_redirected_to_login(self):
        post = self.make_post()
        self.client.logout()
        for url in self.urls_for_post(post):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertRedirects(response, f'{settings.LOGIN_URL}?next={url}', fetch_redirect_response=False)

    def test_regular_users_cannot_read_or_change_reservations(self):
        post = self.make_post()
        self.client.force_login(self.regular_user)
        for url in self.urls_for_post(post):
            for method in (self.client.get, self.client.post):
                with self.subTest(url=url, method=method.__name__):
                    response = method(url, self.form_data())
                    self.assertEqual(response.status_code, 403)
        post.refresh_from_db()
        self.assertEqual(post.content, '開催のお知らせ')
        self.assertEqual(post.status, ScheduledDiscordPost.Status.SCHEDULED)
        self.assertEqual(ScheduledDiscordPost.objects.count(), 1)

    def test_superuser_without_staff_flag_can_access(self):
        self.regular_user.is_superuser = True
        self.regular_user.save(update_fields=['is_superuser'])
        self.client.force_login(self.regular_user)
        response = self.client.get(reverse('discord_scheduler:post_list'))
        self.assertEqual(response.status_code, 200)

    def test_private_pages_disable_caching(self):
        post = self.make_post()
        for url in self.urls_for_post(post):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertIn('private', response.headers['Cache-Control'])
                self.assertIn('no-store', response.headers['Cache-Control'])

    @patch('discord_scheduler.services.requests.post')
    def test_create_saves_only_content_time_and_creator_without_sending(self, send):
        response = self.client.post(
            reverse('discord_scheduler:post_create'),
            self.form_data(status='sent', created_by=self.regular_user.pk, channel_url='https://example.com/'),
        )
        self.assertRedirects(response, reverse('discord_scheduler:post_list'))
        post = ScheduledDiscordPost.objects.get()
        self.assertEqual(post.content, self.form_data()['content'])
        self.assertEqual(post.created_by, self.staff)
        self.assertEqual(post.status, ScheduledDiscordPost.Status.SCHEDULED)
        self.assertEqual(post.scheduled_at, self.future.replace(second=0, microsecond=0))
        send.assert_not_called()

    def test_only_two_fields_are_presented(self):
        response = self.client.get(reverse('discord_scheduler:post_create'))
        self.assertEqual(set(response.context['form'].fields), {'content', 'scheduled_at'})
        self.assertContains(response, '日本時間・JST')
        self.assertContains(response, CHANNEL_URL)
        self.assertNotContains(response, WEBHOOK_URL)

    def test_unconfigured_destination_disables_creation_and_editing(self):
        post = self.make_post()
        with override_settings(DISCORD_SCHEDULED_WEBHOOK_URL=''):
            listing = self.client.get(reverse('discord_scheduler:post_list'))
            self.assertContains(listing, '投稿先の設定が必要です')
            form = self.client.get(reverse('discord_scheduler:post_create'))
            self.assertContains(form, '<fieldset disabled>', html=False)
            for url in [
                reverse('discord_scheduler:post_create'),
                reverse('discord_scheduler:post_edit', args=[post.pk]),
            ]:
                response = self.client.post(url, self.form_data())
                self.assertContains(response, '投稿先の設定が必要です')
        self.assertEqual(ScheduledDiscordPost.objects.count(), 1)
        post.refresh_from_db()
        self.assertEqual(post.content, '開催のお知らせ')

    def test_invalid_destination_is_not_shown_as_a_link(self):
        with override_settings(DISCORD_SCHEDULED_CHANNEL_URL='javascript:alert(1)'):
            response = self.client.get(reverse('discord_scheduler:post_create'))
        self.assertContains(response, '投稿先の設定が必要です')
        self.assertNotContains(response, 'javascript:alert')

    def test_upcoming_posts_are_ordered_and_history_contains_sent_link(self):
        later = self.make_post(scheduled_at=self.future + timedelta(days=1))
        earlier = self.make_post(scheduled_at=self.future)
        sent = self.make_post(
            status=ScheduledDiscordPost.Status.SENT,
            scheduled_at=self.future - timedelta(days=4),
            sent_at=self.future - timedelta(days=4),
            message_url=f'{CHANNEL_URL}/987654321012345678',
        )
        response = self.client.get(reverse('discord_scheduler:post_list'))
        self.assertEqual(list(response.context['pending_posts']), [earlier, later])
        self.assertEqual(list(response.context['history_page']), [sent])
        self.assertContains(response, sent.message_url)

    def test_history_displays_failure_and_ambiguous_delivery(self):
        self.make_post(status=ScheduledDiscordPost.Status.FAILED, error_message='投稿権限を確認してください。')
        self.make_post(status=ScheduledDiscordPost.Status.NEEDS_REVIEW)
        response = self.client.get(reverse('discord_scheduler:post_list'))
        self.assertContains(response, '失敗')
        self.assertContains(response, '投稿権限を確認してください。')
        self.assertContains(response, '要確認')
        self.assertContains(response, '再度予約する前に、Discordのチャンネルを確認してください。')

    def test_content_is_escaped(self):
        self.make_post(content='<script>alert("unsafe")</script>')
        response = self.client.get(reverse('discord_scheduler:post_list'))
        self.assertNotContains(response, '<script>alert("unsafe")</script>')
        self.assertContains(response, '&lt;script&gt;')

    def test_scheduled_post_can_be_edited_without_losing_rate_limit_delay(self):
        retry_at = self.future + timedelta(hours=1)
        post = self.make_post(next_attempt_at=retry_at)
        response = self.client.post(reverse('discord_scheduler:post_edit', args=[post.pk]), self.form_data())
        self.assertRedirects(response, reverse('discord_scheduler:post_list'))
        post.refresh_from_db()
        self.assertEqual(post.content, self.form_data()['content'])
        self.assertEqual(post.next_attempt_at, retry_at)

    def test_non_scheduled_posts_cannot_be_edited_or_cancelled(self):
        for status in (
            ScheduledDiscordPost.Status.SENDING,
            ScheduledDiscordPost.Status.SENT,
            ScheduledDiscordPost.Status.CANCELLED,
            ScheduledDiscordPost.Status.NEEDS_REVIEW,
            ScheduledDiscordPost.Status.FAILED,
        ):
            with self.subTest(status=status):
                post = self.make_post(status=status)
                self.client.post(reverse('discord_scheduler:post_edit', args=[post.pk]), self.form_data())
                self.client.post(reverse('discord_scheduler:post_cancel', args=[post.pk]))
                post.refresh_from_db()
                self.assertEqual(post.status, status)
                self.assertEqual(post.content, '開催のお知らせ')

    def test_edit_does_not_overwrite_a_post_claimed_after_form_validation(self):
        post = self.make_post()
        original_is_valid = ScheduledDiscordPostForm.is_valid

        def claim_after_validation(form):
            valid = original_is_valid(form)
            ScheduledDiscordPost.objects.filter(pk=post.pk).update(status=ScheduledDiscordPost.Status.SENDING)
            return valid

        with patch.object(ScheduledDiscordPostForm, 'is_valid', claim_after_validation):
            response = self.client.post(reverse('discord_scheduler:post_edit', args=[post.pk]), self.form_data())
        self.assertRedirects(response, reverse('discord_scheduler:post_list'))
        post.refresh_from_db()
        self.assertEqual(post.status, ScheduledDiscordPost.Status.SENDING)
        self.assertEqual(post.content, '開催のお知らせ')

    def test_cancel_does_not_overwrite_a_post_claimed_after_page_load(self):
        post = self.make_post()
        original_update = QuerySet.update
        raced = False

        def claim_before_cancel(queryset, **kwargs):
            nonlocal raced
            if queryset.model is ScheduledDiscordPost and kwargs.get('status') == ScheduledDiscordPost.Status.CANCELLED:
                raced = True
                original_update(
                    ScheduledDiscordPost.objects.filter(pk=post.pk), status=ScheduledDiscordPost.Status.SENDING,
                )
            return original_update(queryset, **kwargs)

        with patch.object(QuerySet, 'update', claim_before_cancel):
            self.client.post(reverse('discord_scheduler:post_cancel', args=[post.pk]))
        post.refresh_from_db()
        self.assertTrue(raced)
        self.assertEqual(post.status, ScheduledDiscordPost.Status.SENDING)

    def test_get_cancel_only_shows_confirmation_and_post_cancels(self):
        post = self.make_post()
        url = reverse('discord_scheduler:post_cancel', args=[post.pk])
        response = self.client.get(url)
        self.assertContains(response, 'この投稿予約を取り消しますか？')
        post.refresh_from_db()
        self.assertEqual(post.status, ScheduledDiscordPost.Status.SCHEDULED)
        with override_settings(DISCORD_SCHEDULED_WEBHOOK_URL=''):
            self.client.post(url)
        post.refresh_from_db()
        self.assertEqual(post.status, ScheduledDiscordPost.Status.CANCELLED)

    def test_cancel_preserves_the_fixed_destination_rate_limit_delay(self):
        retry_at = timezone.now() + timedelta(minutes=5)
        post = self.make_post(next_attempt_at=retry_at)
        response = self.client.post(reverse('discord_scheduler:post_cancel', args=[post.pk]))
        self.assertRedirects(response, reverse('discord_scheduler:post_list'))
        post.refresh_from_db()
        self.assertEqual(post.status, ScheduledDiscordPost.Status.CANCELLED)
        self.assertEqual(post.next_attempt_at, retry_at)

    def test_create_edit_and_cancel_require_csrf(self):
        post = self.make_post()
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        for url in [
            reverse('discord_scheduler:post_create'),
            reverse('discord_scheduler:post_edit', args=[post.pk]),
            reverse('discord_scheduler:post_cancel', args=[post.pk]),
        ]:
            with self.subTest(url=url):
                response = csrf_client.post(url, self.form_data())
                self.assertEqual(response.status_code, 403)
        post.refresh_from_db()
        self.assertEqual(post.status, ScheduledDiscordPost.Status.SCHEDULED)
        self.assertEqual(post.content, '開催のお知らせ')
        self.assertEqual(ScheduledDiscordPost.objects.count(), 1)


@override_settings(REQUEST_TOKEN='test-scheduler-request-token')
class ProcessPostsEndpointTests(SimpleTestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.url = reverse('discord_scheduler:process_posts')

    @patch('discord_scheduler.views.process_scheduled_posts')
    def test_only_post_is_allowed(self, process):
        response = self.client.get(self.url, HTTP_REQUEST_TOKEN='test-scheduler-request-token')
        self.assertEqual(response.status_code, 405)
        process.assert_not_called()

    @patch('discord_scheduler.views.process_scheduled_posts')
    def test_missing_or_wrong_token_is_rejected(self, process):
        for token in ('', 'wrong-token'):
            with self.subTest(token=token):
                response = self.client.post(self.url, HTTP_REQUEST_TOKEN=token)
                self.assertEqual(response.status_code, 401)
        process.assert_not_called()

    @override_settings(REQUEST_TOKEN='')
    @patch('discord_scheduler.views.process_scheduled_posts')
    def test_empty_server_token_never_authorizes_a_request(self, process):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 401)
        process.assert_not_called()

    @patch('discord_scheduler.views.process_scheduled_posts')
    def test_valid_token_runs_without_session_or_csrf_cookie(self, process):
        counts = {'processed': 1, 'sent': 1, 'failed': 0, 'needs_review': 0, 'deferred': 0}
        process.return_value = counts
        response = self.client.post(self.url, HTTP_REQUEST_TOKEN='test-scheduler-request-token')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), counts)
        process.assert_called_once_with(limit=20)
