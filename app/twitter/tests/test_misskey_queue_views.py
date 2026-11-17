"""ポストキューの Misskey 表示・手動再投稿・重複防止を確認する。"""

from datetime import timedelta
from unittest.mock import patch

from bs4 import BeautifulSoup
from django.db import OperationalError, connection
from django.test import RequestFactory, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from structlog.testing import capture_logs

from tests.factories import make_community
from twitter.models import MisskeyNote, TweetQueue
from twitter.tests.test_access_control import (
    ANONYMOUS, OTHER, OWNER, STAFF, SUPERUSER, TwitterAccessControlTestBase,
)
from twitter.views import TweetQueueListView


SUCCESS = {'ok': True, 'data': {'id': 'note-878'}, 'status_code': 200, 'error_body': None}
FAILURE = {
    'ok': False, 'data': None, 'status_code': 403, 'error_body': 'ACCESS_DENIED',
    'retryable': False,
}
UNKNOWN = {
    'ok': False, 'data': None, 'status_code': None, 'error_body': 'UNKNOWN_ERROR',
    'retryable': False, 'outcome_unknown': True,
}
ENV = {'MISSKEY_TOKEN': 'dummy-test-token', 'MISSKEY_HOST': 'social.example.org'}
WARNING = '結果不明のため、すでに投稿されている可能性があります'
LABELS = {
    'pending': '未送信', 'sending': '未送信', 'posted': '投稿済み',
    'unknown': '結果不明', 'failed': '失敗', 'skipped': 'スキップ',
}


@patch.dict('os.environ', ENV)
class MisskeyQueueViewsTest(TwitterAccessControlTestBase):
    def setUp(self):
        super().setUp()
        self.login_as(SUPERUSER)
        self.list_url = reverse('twitter:tweet_queue_list')
        self.detail_url = reverse('twitter:tweet_queue_detail', args=[self.queue.pk])
        self.post_note = self.enterContext(patch('twitter.misskey_api.post_note', return_value=SUCCESS))
        self.notify = self.enterContext(patch('twitter.services.misskey_service.notify_misskey_post_failure'))
        self.x_post = self.enterContext(patch('twitter.views.post_tweet'))

    def note(self, status='failed', **values):
        return MisskeyNote.objects.create(tweet_queue=self.queue, status=status, **values)

    def repost(self, **kwargs):
        return self.client.post(self.detail_url, {'action': 'repost_misskey'}, **kwargs)

    def assert_x_unchanged(self, before):
        self.assertEqual(TweetQueue.objects.values().get(pk=self.queue.pk), before)
        self.x_post.assert_not_called()

    def test_list_and_detail_show_all_states_and_repost_controls(self):
        note = self.note()
        for status, label in LABELS.items():
            with self.subTest(status=status):
                note.status = status
                note.save(update_fields=['status'])
                for url in (self.list_url, self.detail_url):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 200)
                    soup = BeautifulSoup(response.content, 'html.parser')
                    chip = soup.select_one('[data-queue-misskey-status] .detail-chip')
                    self.assertIsNotNone(chip)
                    self.assertEqual(chip.get_text(strip=True), label)
                    if url == self.detail_url:
                        allowed = status in ('failed', 'unknown', 'skipped')
                        self.assertEqual(bool(soup.select('[data-confirm-panel=repost-misskey]')), allowed)
                        self.assertEqual(bool(soup.select('button[value=repost_misskey]')), allowed)
                        self.assertEqual(WARNING in soup.get_text(), status == 'unknown')
                        if allowed:
                            form = soup.select_one('#repost-misskey form')
                            self.assertEqual(form['method'], 'post')
                            self.assertEqual(form['action'], self.detail_url)
                            self.assertIsNotNone(form.select_one('[name=csrfmiddlewaretoken]'))
                            self.assertIsNone(form.select_one('form'))
                self.post_note.assert_not_called()

    def test_missing_note_is_out_of_scope_and_post_does_not_create_it(self):
        for url in (self.list_url, self.detail_url):
            response = self.client.get(url)
            soup = BeautifulSoup(response.content, 'html.parser')
            self.assertEqual(soup.select_one('[data-queue-misskey-status]').get_text(strip=True).removeprefix('Misskey'), '対象外')
            self.assertIsNone(soup.select_one('button[value=repost_misskey]'))
        response = self.repost(follow=True)
        self.assertContains(response, 'このキューは Misskey 投稿の対象外です。')
        self.assertFalse(MisskeyNote.objects.filter(tweet_queue=self.queue).exists())
        self.post_note.assert_not_called()

    def test_detail_shows_link_attempts_post_time_and_escaped_error(self):
        note = self.note('posted', note_id='note-878', posted_at=timezone.now(),
                         attempts=3, error_message='<script>unsafe</script>')
        response = self.client.get(self.detail_url)
        soup = BeautifulSoup(response.content, 'html.parser')
        section = soup.select_one('[aria-labelledby=misskey-heading]')
        link = section.select_one('a[target=_blank]')
        self.assertEqual(link['href'], 'https://social.example.org/notes/note-878')
        self.assertIn('noopener', link['rel'])
        self.assertIn('noreferrer', link['rel'])
        self.assertIn('3 回', section.get_text())
        self.assertIn(timezone.localtime(note.posted_at).strftime('%Y/%m/%d %H:%M'), section.get_text())
        self.assertContains(response, '&lt;script&gt;unsafe&lt;/script&gt;')
        self.assertNotContains(response, '<script>unsafe</script>')

    def test_note_link_requires_posted_status_and_id(self):
        note = self.note('unknown', note_id='note-878')
        for status, note_id in (('unknown', 'note-878'), ('posted', '')):
            with self.subTest(status=status, note_id=note_id):
                note.status, note.note_id = status, note_id
                note.save()
                self.assertNotContains(self.client.get(self.detail_url), 'https://social.example.org/notes/')

    @patch.dict('os.environ', {'MISSKEY_HOST': 'invalid.example/path'})
    def test_invalid_host_does_not_render_a_note_link(self):
        self.note('posted', note_id='note-878')
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'invalid.example/path')

    def test_note_id_is_encoded_in_url(self):
        self.note('posted', note_id='id/?q=#fragment')
        response = self.client.get(self.detail_url)
        self.assertContains(response, 'https://social.example.org/notes/id%2F%3Fq%3D%23fragment')

    def test_disabled_integration_hides_controls_and_rejects_post(self):
        note = self.note()
        before = TweetQueue.objects.values().get(pk=self.queue.pk)
        for token in ('', '   '):
            with self.subTest(token=token), patch.dict('os.environ', {'MISSKEY_TOKEN': token}):
                for url in (self.list_url, self.detail_url):
                    response = self.client.get(url)
                    self.assertContains(response, 'Misskey 連携は無効')
                    soup = BeautifulSoup(response.content, 'html.parser')
                    self.assertIn('連携無効', soup.select_one('[data-queue-misskey-status]').get_text())
                    self.assertIsNone(soup.select_one('button[value=repost_misskey]'))
                self.assertContains(self.repost(follow=True), 'Misskey 連携は無効です。')
        note.refresh_from_db()
        self.assertEqual(note.status, 'failed')
        self.assert_x_unchanged(before)
        self.post_note.assert_not_called()

    def test_queryset_loads_notes_in_one_query_including_missing_notes(self):
        self.note()
        for status in LABELS:
            queue = TweetQueue.objects.create(
                community=self.community, tweet_type='new_community',
                scheduled_at=timezone.now(), status='ready',
            )
            MisskeyNote.objects.create(tweet_queue=queue, status=status)
        TweetQueue.objects.create(community=self.community, tweet_type='new_community', scheduled_at=timezone.now())
        view = TweetQueueListView()
        view.request = RequestFactory().get(self.list_url)
        view.request.user = self.superuser
        with self.assertNumQueries(1):
            notes = [getattr(queue, 'misskey_note', None) for queue in view.get_queryset()]
        self.assertEqual(len(notes), 8)
        self.assertEqual(notes.count(None), 1)

    def test_each_allowed_state_reposts_immediately_and_resets_attempts(self):
        note = self.note(attempts=3, error_message='前回の失敗')
        # X が投稿済みでも Misskey だけ再送でき、未来の予約日時も待たない。
        self.queue.status = 'posted'
        self.queue.tweet_id = 'x-post'
        self.queue.posted_at = timezone.now()
        self.queue.save()
        before = TweetQueue.objects.values().get(pk=self.queue.pk)
        for status in ('failed', 'unknown', 'skipped'):
            with self.subTest(status=status):
                note.status, note.attempts = status, 3
                note.save()
                self.post_note.reset_mock()

                def observe_sending(*args, **kwargs):
                    stored = MisskeyNote.objects.get(pk=note.pk)
                    self.assertEqual(stored.status, 'sending')
                    self.assertEqual(stored.attempts, 1)
                    return SUCCESS

                self.post_note.side_effect = observe_sending
                with capture_logs() as logs:
                    response = self.repost(follow=True)
                self.assertContains(response, 'Misskey に再投稿しました。')
                self.assertEqual(response.redirect_chain, [(self.detail_url, 302)])
                note.refresh_from_db()
                self.assertEqual((note.status, note.note_id, note.attempts), ('posted', 'note-878', 1))
                self.assertIsNotNone(note.posted_at)
                self.assertEqual(note.error_message, '')
                self.post_note.assert_called_once_with(self.queue.generated_text, image_url=None)
                self.assert_x_unchanged(before)
                self.assertEqual([log['status'] for log in logs if log['event'] == 'misskey_post'], ['posted'])
        self.notify.assert_not_called()

    def test_failure_and_unknown_update_only_misskey_and_notify(self):
        note = self.note(attempts=3)
        before = TweetQueue.objects.values().get(pk=self.queue.pk)
        for result, expected in ((FAILURE, 'failed'), (UNKNOWN, 'unknown'),
                                 ({**FAILURE, 'retryable': True}, 'pending')):
            with self.subTest(status=expected):
                note.status = 'failed'
                note.save()
                self.post_note.return_value = result
                self.notify.reset_mock()
                response = self.repost(follow=True)
                note.refresh_from_db()
                self.assertEqual(note.status, expected)
                self.assertEqual(note.attempts, 1)
                self.assertTrue(note.error_message)
                self.assertContains(response, note.error_message)
                self.notify.assert_called_once_with(self.queue, result)
                self.assert_x_unchanged(before)

    def test_client_exception_marks_unknown(self):
        note = self.note()
        self.post_note.side_effect = RuntimeError('client interrupted')
        self.repost()
        note.refresh_from_db()
        self.assertEqual(note.status, 'unknown')
        self.notify.assert_called_once()

    def test_non_superusers_are_forbidden_and_have_no_repost_controls(self):
        note = self.note()
        for role in (OWNER, STAFF, OTHER, ANONYMOUS):
            with self.subTest(role=role):
                self.login_as(role)
                if role in (OWNER, STAFF):
                    response = self.client.get(self.detail_url)
                    self.assertEqual(response.status_code, 200)
                    self.assertNotContains(response, 'value="repost_misskey"')
                    self.assertNotContains(response, 'data-confirm-panel="repost-misskey"')
                response = self.repost()
                self.assertEqual(response.status_code, 302 if role == ANONYMOUS else 403)
        note.refresh_from_db()
        self.assertEqual(note.status, 'failed')
        self.post_note.assert_not_called()

    def test_posted_sending_pending_reject_repost(self):
        note = self.note()
        for status in ('posted', 'sending', 'pending'):
            with self.subTest(status=status):
                note.status = status
                note.save()
                before = MisskeyNote.objects.values().get(pk=note.pk)
                self.assertContains(self.repost(follow=True), 'この状態では Misskey に再投稿できません。')
                self.assertEqual(MisskeyNote.objects.values().get(pk=note.pk), before)
        self.post_note.assert_not_called()

    def test_empty_and_unfinished_generation_are_rejected(self):
        note = self.note(attempts=3)
        for status, text, message in (
            ('ready', '', '生成テキストが空'), ('ready', '  \n ', '生成テキストが空'),
            ('generating', '古い本文', '投稿文の生成が完了していない'),
            ('generation_failed', '古い本文', '投稿文の生成が完了していない'),
        ):
            with self.subTest(status=status, text=text):
                TweetQueue.objects.filter(pk=self.queue.pk).update(status=status, generated_text=text)
                self.assertContains(self.repost(follow=True), message)
                note.refresh_from_db()
                self.assertEqual((note.status, note.attempts), ('failed', 3))
        self.post_note.assert_not_called()

    def test_get_with_action_cannot_repost(self):
        self.note()
        self.assertEqual(self.client.get(self.detail_url, {'action': 'repost_misskey'}).status_code, 200)
        self.post_note.assert_not_called()

    def test_second_request_during_send_and_after_success_cannot_duplicate(self):
        note = self.note()

        def second_request(*args, **kwargs):
            self.assertContains(self.repost(follow=True), 'この状態では Misskey に再投稿できません。')
            return SUCCESS

        self.post_note.side_effect = second_request
        self.repost()
        self.assertContains(self.repost(follow=True), 'この状態では Misskey に再投稿できません。')
        note.refresh_from_db()
        self.assertEqual(note.status, 'posted')
        self.post_note.assert_called_once()


@patch.dict('os.environ', ENV)
class MisskeyRepostCommitTest(TransactionTestCase):
    def test_claim_is_committed_before_api_and_failed_result_save_prevents_resend(self):
        community = make_community(status='pending')
        queue = TweetQueue.objects.create(
            community=community, tweet_type='new_community', status='posted',
            generated_text='再投稿する本文', scheduled_at=timezone.now() + timedelta(days=1),
        )
        note = MisskeyNote.objects.create(tweet_queue=queue, status='failed', attempts=3)
        original_save = MisskeyNote.save

        def fail_result_save(instance, *args, **kwargs):
            if instance.status == 'posted':
                raise OperationalError('result save interrupted')
            return original_save(instance, *args, **kwargs)

        def observe_committed(*args, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            self.assertEqual(MisskeyNote.objects.get(pk=note.pk).status, 'sending')
            return SUCCESS

        from twitter.services.misskey_service import repost_note

        with patch('twitter.misskey_api.post_note', side_effect=observe_committed) as post_note:
            with patch.object(MisskeyNote, 'save', fail_result_save):
                with self.assertRaises(OperationalError):
                    repost_note(queue.pk)
            note.refresh_from_db()
            self.assertEqual(note.status, 'sending')
            with self.assertRaisesMessage(ValueError, 'この状態では Misskey に再投稿できません。'):
                repost_note(queue.pk)
            post_note.assert_called_once()
