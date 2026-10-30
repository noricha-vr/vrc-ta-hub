"""X と独立した Misskey のキュー処理・期限・再試行を検証する。"""

from datetime import timedelta
from unittest.mock import Mock, patch

from django.db import IntegrityError, OperationalError, transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from structlog.testing import capture_logs

from twitter.models import MisskeyNote, TweetQueue
from twitter.notifications import notify_misskey_post_failure
from twitter.services.misskey_service import process_pending_notes
from twitter.services.tweet_scheduling_service import process_scheduled_tweets
from twitter.tests._auto_tweet_test_base import AutoTweetTestBase
from twitter.tests.test_misskey_api import api_response

SUCCESS = {'ok': True, 'data': {'id': 'note-123'}, 'status_code': 200, 'error_body': None}
FAILURE = {'ok': False, 'data': None, 'status_code': 503, 'error_body': 'unavailable'}
UNKNOWN = {
    'ok': False, 'data': None, 'status_code': None,
    'error_body': 'UNKNOWN_ERROR', 'retryable': False, 'outcome_unknown': True,
}
ENV = {
    'MISSKEY_TOKEN': 'test-misskey-token', 'MISSKEY_HOST': 'misskey.io',
    'MISSKEY_VISIBILITY': 'public', 'MISSKEY_API_ALLOW_TEST_CALLS': '1',
    'REQUEST_TOKEN': 'test-token',
}


@patch.dict('os.environ', ENV)
class MisskeySchedulingTest(AutoTweetTestBase):
    def setUp(self):
        super().setUp()
        TweetQueue.objects.all().delete()
        self.current = timezone.now()
        self.note_patch = patch('twitter.misskey_api.post_note', return_value=SUCCESS)
        self.note_post = self.note_patch.start()
        self.addCleanup(self.note_patch.stop)
        self.notify = patch('twitter.services.misskey_service.notify_misskey_post_failure').start()
        self.addCleanup(patch.stopall)
        self.x_post = Mock(return_value=SUCCESS)
        self.x_notify = Mock()
        self.x_upload = Mock(return_value='x-media')

    def queue(self, **kwargs):
        """投稿対象の X キューを作る。"""
        values = {
            'tweet_type': 'new_community', 'community': self.community, 'event': self.event,
            'status': 'ready', 'scheduled_at': self.current - timedelta(minutes=5),
            'generated_text': '名前さん（@foo） #VRChat',
        }
        values.update(kwargs)
        return TweetQueue.objects.create(**values)

    def process(self, **kwargs):
        return process_scheduled_tweets(
            now=self.current, post_tweet_func=self.x_post,
            upload_media_func=self.x_upload, notify_failure_func=self.x_notify, **kwargs,
        )

    def test_x_failure_still_posts_to_misskey_with_mocked_http(self):
        queue = self.queue()
        self.x_post.return_value = FAILURE
        self.note_patch.stop()
        with patch('twitter.misskey_api.requests.post') as http_post:
            http_post.return_value = api_response({'createdNote': {'id': 'independent-note'}})
            self.process()
        queue.refresh_from_db()
        note = queue.misskey_note
        self.assertEqual(queue.status, 'failed')
        self.assertEqual(note.status, 'posted')
        self.assertEqual(note.note_id, 'independent-note')
        self.assertIsNotNone(note.posted_at)
        self.assertEqual(note.attempts, 1)
        self.assertEqual(http_post.call_args.kwargs['json']['text'], '名前さん（https://x.com/foo） #VRChat')
        self.assertTrue(http_post.call_args.kwargs['json']['noExtractMentions'])
        self.x_notify.assert_called_once()

    @override_settings(TESTING=False)
    @patch.dict('os.environ', {'MISSKEY_API_ALLOW_TEST_CALLS': ''})
    @patch('sys.argv', ['manage.py', 'test', 'twitter'])
    def test_finally_stage_cannot_send_when_only_x_is_mocked(self):
        queue = self.queue(image_url='https://data.vrc-ta-hub.com/poster.png')
        self.note_patch.stop()
        with patch('twitter.misskey_api.requests.post') as post, \
                patch('twitter.image_download.requests.get') as get:
            self.process()
        queue.refresh_from_db()
        self.assertEqual(queue.status, 'posted')
        self.assertEqual(queue.misskey_note.status, 'pending')
        self.assertEqual(queue.misskey_note.attempts, 1)
        post.assert_not_called()
        get.assert_not_called()

    def test_pending_note_is_created_before_x_post(self):
        queue = self.queue()
        def observe_pending(*args, **kwargs):
            self.assertTrue(MisskeyNote.objects.filter(tweet_queue=queue, status='pending').exists())
            return SUCCESS
        self.x_post.side_effect = observe_pending
        self.process()
        self.assertEqual(queue.misskey_note.status, 'posted')

    def test_note_creation_failure_leaves_x_ready_for_next_cron(self):
        queue = self.queue()
        later = self.queue(scheduled_at=self.current - timedelta(minutes=1))
        with patch('twitter.models.MisskeyNote.objects.get_or_create',
                   side_effect=OperationalError('temporary database failure')):
            with self.assertLogs('twitter.services.tweet_scheduling_service', level='ERROR'):
                result = self.process()
        queue.refresh_from_db()
        later.refresh_from_db()
        self.assertEqual(queue.status, 'ready')
        self.assertEqual(later.status, 'ready')
        self.assertFalse(result['posted_attempted'])
        self.assertFalse(MisskeyNote.objects.exists())
        self.x_post.assert_not_called()
        self.x_upload.assert_not_called()
        self.note_post.assert_not_called()
        self.process()
        queue.refresh_from_db()
        self.assertEqual(queue.status, 'posted')
        self.assertEqual(queue.misskey_note.status, 'posted')
        self.x_post.assert_called_once()
        self.note_post.assert_called_once()

    def test_retry_rechecks_event_date_even_when_x_already_finished(self):
        # JST 00:01: a reminder from 23:59 is not expired, but is no longer current.
        self.current = timezone.make_aware(timezone.datetime(2026, 10, 30, 0, 1))
        today = timezone.localdate(self.current)
        cases = [
            ('lt', today), ('special', today),
            ('lt', today - timedelta(days=1)),
            ('special', today - timedelta(days=1)),
            ('daily_reminder', today - timedelta(days=1)),
            ('daily_reminder', today + timedelta(days=1)),
        ]
        for status in ('posted', 'failed'):
            for tweet_type, event_date in cases:
                with self.subTest(status=status, tweet_type=tweet_type, event_date=event_date):
                    TweetQueue.objects.all().delete()
                    self.event.date = event_date
                    self.event.save(update_fields=['date'])
                    queue = self.queue(status=status, tweet_type=tweet_type,
                                       scheduled_at=self.current - timedelta(minutes=2))
                    note = MisskeyNote.objects.create(tweet_queue=queue, attempts=1)
                    process_pending_notes(now=self.current)
                    note.refresh_from_db()
                    queue.refresh_from_db()
                    self.assertEqual(note.status, 'skipped')
                    self.assertEqual(note.attempts, 1)
                    self.assertEqual(queue.status, status)
        self.note_post.assert_not_called()
        self.notify.assert_not_called()

    def test_retry_allows_current_reminder_and_future_individual_announcements(self):
        today = timezone.localdate(self.current)
        for tweet_type, event_date in [
            ('daily_reminder', today),
            ('lt', today + timedelta(days=1)),
            ('special', today + timedelta(days=1)),
        ]:
            with self.subTest(tweet_type=tweet_type):
                self.event.date = event_date
                self.event.save(update_fields=['date'])
                note = MisskeyNote.objects.create(tweet_queue=self.queue(
                    status='failed', tweet_type=tweet_type,
                ))
                process_pending_notes(now=self.current)
                note.refresh_from_db()
                self.assertEqual(note.status, 'posted')

    def test_unexpected_x_exception_still_posts_to_misskey(self):
        queue = self.queue()
        self.x_post.side_effect = RuntimeError('X client error')
        with self.assertRaises(RuntimeError):
            self.process()
        self.assertEqual(queue.misskey_note.status, 'posted')
        self.note_post.assert_called_once()

    def test_misskey_failure_does_not_change_x_success(self):
        queue = self.queue(image_url='https://data.vrc-ta-hub.com/poster.png')
        self.note_post.return_value = FAILURE
        self.process()
        queue.refresh_from_db()
        self.assertEqual(queue.status, 'posted')
        self.assertEqual(queue.misskey_note.status, 'pending')
        self.assertEqual(queue.misskey_note.attempts, 1)
        self.note_post.assert_called_once_with(
            '名前さん（https://x.com/foo） #VRChat', image_url=queue.image_url,
        )
        self.notify.assert_called_once_with(queue, FAILURE)

    def test_unknown_result_is_saved_not_retried_and_notified_for_manual_check(self):
        queue = self.queue()
        self.note_post.return_value = UNKNOWN

        self.process()
        self.process()

        note = queue.misskey_note
        note.refresh_from_db()
        queue.refresh_from_db()
        self.assertEqual(note.status, 'unknown')
        self.assertEqual(note.get_status_display(), '結果不明')
        self.assertEqual(note.attempts, 1)
        self.assertIn('自動再送しません', note.error_message)
        self.assertEqual(queue.status, 'posted')
        self.note_post.assert_called_once()
        self.notify.assert_called_once_with(queue, UNKNOWN)

    def test_misskey_retry_continues_without_x_ready_queue(self):
        queue = self.queue(status='failed')
        note = MisskeyNote.objects.create(tweet_queue=queue, attempts=1, error_message='old error')
        self.process()
        note.refresh_from_db()
        queue.refresh_from_db()
        self.assertEqual(note.status, 'posted')
        self.assertEqual(note.attempts, 2)
        self.assertEqual(note.error_message, '')
        self.assertEqual(queue.status, 'failed')
        self.x_post.assert_not_called()

    def test_failed_attempts_stop_at_three(self):
        note = MisskeyNote.objects.create(tweet_queue=self.queue(status='failed'))
        self.note_post.return_value = FAILURE
        for attempt in range(1, 4):
            self.process()
            note.refresh_from_db()
            self.assertEqual(note.attempts, attempt)
            self.assertEqual(note.status, 'failed' if attempt == 3 else 'pending')
        self.process()
        self.assertEqual(self.note_post.call_count, 3)
        self.assertEqual(self.notify.call_count, 3)
        self.assertIn('503', note.error_message)

    def test_unexpected_client_exception_is_unknown_and_not_retried(self):
        note = MisskeyNote.objects.create(tweet_queue=self.queue(status='posted'))
        self.note_post.side_effect = RuntimeError('unexpected')
        for _ in range(3):
            self.process()
        note.refresh_from_db()
        self.assertEqual(note.status, 'unknown')
        self.assertEqual(note.attempts, 1)
        self.note_post.assert_called_once()

    def test_pending_expires_after_24_hours_even_when_x_failed(self):
        note = MisskeyNote.objects.create(tweet_queue=self.queue(
            status='failed', scheduled_at=self.current - timedelta(hours=24, seconds=1),
        ))
        self.process()
        note.refresh_from_db()
        self.assertEqual(note.status, 'skipped')
        self.assertEqual(note.attempts, 0)
        self.note_post.assert_not_called()
        self.notify.assert_not_called()

    def test_expiry_is_based_on_scheduled_at_not_note_creation(self):
        note = MisskeyNote.objects.create(tweet_queue=self.queue(status='failed'))
        MisskeyNote.objects.filter(pk=note.pk).update(created_at=self.current - timedelta(days=2))
        self.process()
        note.refresh_from_db()
        self.assertEqual(note.status, 'posted')

    def test_exactly_24_hours_is_still_within_existing_x_boundary(self):
        note = MisskeyNote.objects.create(tweet_queue=self.queue(
            status='failed', scheduled_at=self.current - timedelta(hours=24),
        ))
        self.process()
        note.refresh_from_db()
        self.assertEqual(note.status, 'posted')

    def test_only_one_pending_note_is_attempted_per_cron(self):
        older = MisskeyNote.objects.create(tweet_queue=self.queue(status='posted'))
        newer_queue = self.queue(scheduled_at=self.current - timedelta(minutes=1))
        self.process()
        older.refresh_from_db()
        self.assertEqual(older.status, 'posted')
        self.assertEqual(newer_queue.misskey_note.status, 'pending')
        self.assertEqual(newer_queue.misskey_note.attempts, 0)
        self.note_post.assert_called_once()
        self.x_post.assert_called_once()

    def test_expired_notes_do_not_consume_the_posting_slot(self):
        expired = MisskeyNote.objects.create(tweet_queue=self.queue(
            status='posted', scheduled_at=self.current - timedelta(hours=25),
        ))
        valid = MisskeyNote.objects.create(tweet_queue=self.queue(status='posted'))
        self.process()
        expired.refresh_from_db()
        valid.refresh_from_db()
        self.assertEqual(expired.status, 'skipped')
        self.assertEqual(valid.status, 'posted')
        self.note_post.assert_called_once()

    def test_x_skip_rules_never_create_misskey_notes(self):
        today = timezone.localdate()
        cases = [
            ('new_community', today, self.current - timedelta(hours=25)),
            ('lt', today, self.current),
            ('special', today, self.current),
            ('lt', today - timedelta(days=1), self.current),
            ('special', today - timedelta(days=1), self.current),
            ('daily_reminder', today - timedelta(days=1), self.current),
            ('daily_reminder', today + timedelta(days=1), self.current),
        ]
        for tweet_type, event_date, scheduled_at in cases:
            with self.subTest(tweet_type=tweet_type, event_date=event_date, scheduled_at=scheduled_at):
                TweetQueue.objects.all().delete()
                self.event.date = event_date
                self.event.save(update_fields=['date'])
                self.queue(tweet_type=tweet_type, scheduled_at=scheduled_at)
                self.process()
                self.assertFalse(MisskeyNote.objects.exists())
        self.note_post.assert_not_called()
        self.x_post.assert_not_called()

    def test_existing_pending_for_x_skipped_queue_is_skipped(self):
        note = MisskeyNote.objects.create(tweet_queue=self.queue(status='skipped'))
        self.process()
        note.refresh_from_db()
        self.assertEqual(note.status, 'skipped')
        self.note_post.assert_not_called()

    def test_future_queue_does_not_create_note(self):
        self.queue(scheduled_at=self.current + timedelta(minutes=5))
        self.process()
        self.assertFalse(MisskeyNote.objects.exists())
        self.note_post.assert_not_called()

    @patch.dict('os.environ', {'MISSKEY_TOKEN': ''})
    def test_missing_token_disables_misskey_and_preserves_x(self):
        queue = self.queue()
        with self.assertNumQueries(0):
            process_pending_notes(now=self.current)
        with patch('twitter.models.MisskeyNote.objects.get_or_create') as create_note:
            self.process()
        self.assertFalse(MisskeyNote.objects.exists())
        self.note_post.assert_not_called()
        create_note.assert_not_called()
        queue.refresh_from_db()
        self.assertEqual(queue.status, 'posted')
        self.x_post.assert_called_once()

    @patch.dict('os.environ', {'MISSKEY_TOKEN': ''})
    def test_disabled_processing_leaves_existing_pending_untouched(self):
        note = MisskeyNote.objects.create(tweet_queue=self.queue(status='failed'))
        self.process()
        note.refresh_from_db()
        self.assertEqual(note.status, 'pending')
        self.assertEqual(note.attempts, 0)
        self.note_post.assert_not_called()

    def test_multiple_crons_do_not_duplicate_posted_note(self):
        queue = self.queue()
        self.process()
        self.process()
        self.note_post.assert_called_once()
        self.assertEqual(MisskeyNote.objects.filter(tweet_queue=queue).count(), 1)

    def test_note_is_one_to_one_with_queue(self):
        queue = self.queue()
        MisskeyNote.objects.create(tweet_queue=queue)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MisskeyNote.objects.create(tweet_queue=queue)

    def test_structured_log_contains_observation_fields(self):
        queue = self.queue()
        with capture_logs() as logs:
            self.process()
        posts = [entry for entry in logs if entry['event'] == 'misskey_post']
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]['queue_id'], queue.pk)
        self.assertEqual(posts[0]['tweet_type'], queue.tweet_type)
        self.assertEqual(posts[0]['status'], 'posted')
        self.assertEqual(posts[0]['http_status'], 200)
        self.assertNotIn('text', posts[0])

    @override_settings(DISCORD_WEBHOOK_URL='https://discord.invalid/offline-test')
    @patch('twitter.notifications.post_discord_webhook')
    def test_misskey_failure_notifies_existing_admin_webhook(self, webhook):
        notify_misskey_post_failure(self.queue(), FAILURE)
        webhook.assert_called_once()
        self.assertEqual(webhook.call_args.args[0], 'https://discord.invalid/offline-test')
        self.assertIn('Misskey 投稿に失敗', webhook.call_args.args[1]['content'])
        self.assertIn('503', str(webhook.call_args.args[1]))

    @override_settings(DISCORD_WEBHOOK_URL='https://discord.invalid/offline-test')
    @patch('twitter.notifications.post_discord_webhook')
    def test_unknown_result_notification_requires_manual_check(self, webhook):
        notify_misskey_post_failure(self.queue(), UNKNOWN)
        message = webhook.call_args.args[1]
        self.assertIn('投稿結果が不明', message['content'])
        self.assertIn('投稿されたか手動で確認してください', message['content'])
        self.assertIn('投稿結果不明', message['embeds'][0]['title'])

    @override_settings(DISCORD_WEBHOOK_URL='')
    @patch('twitter.notifications.post_discord_webhook')
    def test_missing_admin_webhook_disables_notification(self, webhook):
        notify_misskey_post_failure(self.queue(), FAILURE)
        webhook.assert_not_called()

    @patch('twitter.views.post_tweet', return_value=FAILURE)
    @patch('twitter.views.notify_tweet_post_failure')
    def test_cron_view_invokes_independent_misskey_stage(self, x_notify, x_post):
        queue = self.queue()
        response = self.client.get(reverse('twitter:post_scheduled_tweets'), HTTP_REQUEST_TOKEN='test-token')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(queue.misskey_note.status, 'posted')
        x_post.assert_called_once()
