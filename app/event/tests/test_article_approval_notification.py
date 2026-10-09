"""承認で公開になった記事の通知を、審査ページと一覧の両方で確かめる。"""
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from event.models import EventDetail
from event.notifications import notify_applicant_of_result
from event.services.article_generation import _notify_first_time, notify_article_on_approval
from tests.factories import make_community, make_discord_linked_user, make_event, make_event_detail


@patch('event.notifications.post_discord_webhook')
@patch('event.notifications.send_mail', return_value=1)
class ArticleApprovalNotificationTest(TestCase):
    """記事の通知は承認の 1 回だけ。承認前のメール送信状況で送り方を変える。"""

    def setUp(self):
        self.owner = make_discord_linked_user(user_name='owner', email='owner@example.com')
        self.applicant = make_discord_linked_user(user_name='speaker', email='speaker@example.com')
        self.community = make_community(
            owner=self.owner, webhook_url='https://discord.com/api/webhooks/123/token',
        )
        self.event = make_event(self.community)
        self.client.force_login(self.owner)

    def _detail(self, **changes):
        values = {
            'applicant': self.applicant,
            'status': 'pending',
            'article_consent': EventDetail.ArticleConsent.OK,
            'h1': '発表の記事',
            'contents': '記事の本文',
        }
        values.update(changes)
        return make_event_detail(self.event, **values)

    def _post(self, detail, endpoint, action='approve'):
        data = {
            'action': action,
            'event': detail.event_id,
            'start_time': detail.start_time.strftime('%H:%M'),
            'duration': detail.duration,
            'rejection_reason': '集会の趣旨と合いません' if action == 'reject' else '',
        }
        return self.client.post(reverse(f'event:{endpoint}', kwargs={'pk': detail.pk}), data)

    def _article_posts(self, post_webhook):
        return [
            call.args[1]['embeds'][0] for call in post_webhook.call_args_list
            if call.args[1]['embeds'][0]['title'] == '📝 発表の記事を公開しました'
        ]

    def _result_email(self, send_mail):
        return next(
            call.kwargs['html_message'] for call in send_mail.call_args_list
            if '発表申請が承認されました' in call.kwargs['subject']
        )

    def test_previously_emailed_article_only_posts_discord_once(self, send_mail, post_webhook):
        """作成メール済みなら公開メールを重ねず、再度の承認でも記事を流さない。"""
        for endpoint in ('lt_application_review', 'lt_application_approve'):
            with self.subTest(endpoint=endpoint):
                detail = self._detail()
                _notify_first_time(detail.pk)
                detail.refresh_from_db()
                notified_at = detail.article_published_notified_at
                self.assertIsNotNone(notified_at)
                self.assertIn('作成しました', send_mail.call_args.kwargs['subject'])
                self.assertIn('それまでに編集ページで直せます', send_mail.call_args.kwargs['html_message'])
                post_webhook.assert_not_called()
                send_mail.reset_mock()

                response = self._post(detail, endpoint)

                self.assertEqual(response.status_code, 302)
                send_mail.assert_called_once()
                self.assertIn('発表申請が承認されました', send_mail.call_args.kwargs['subject'])
                posts = self._article_posts(post_webhook)
                self.assertEqual(len(posts), 1)
                self.assertIn(reverse('event:detail', kwargs={'pk': detail.pk}), posts[0]['url'])
                html = self._result_email(send_mail)
                self.assertIn('記事も公開しました', html)
                self.assertIn(reverse('event:detail', kwargs={'pk': detail.pk}), html)
                self.assertIn(reverse('account:lt_application_edit', kwargs={'pk': detail.pk}), html)
                detail.refresh_from_db()
                self.assertEqual(detail.article_published_notified_at, notified_at)

                for repeated_endpoint in ('lt_application_review', 'lt_application_approve'):
                    self._post(detail, repeated_endpoint)
                self.assertEqual(len(self._article_posts(post_webhook)), 1)
                send_mail.assert_called_once()
                send_mail.reset_mock()
                post_webhook.reset_mock()

    def test_unnotified_article_sends_publication_email_and_discord_once(self, send_mail, post_webhook):
        """作成時にメールを送れていなければ、承認時に初回の公開通知を送る。"""
        for endpoint in ('lt_application_review', 'lt_application_approve'):
            with self.subTest(endpoint=endpoint):
                detail = self._detail()
                send_mail.return_value = 0
                _notify_first_time(detail.pk)
                detail.refresh_from_db()
                self.assertIsNone(detail.article_published_notified_at)
                post_webhook.assert_not_called()
                send_mail.reset_mock()
                send_mail.return_value = 1

                self.assertEqual(self._post(detail, endpoint).status_code, 302)

                publication_emails = [
                    call for call in send_mail.call_args_list
                    if '発表の記事を公開しました' in call.kwargs['subject']
                ]
                self.assertEqual(len(publication_emails), 1)
                self.assertEqual(send_mail.call_count, 2)
                self.assertEqual(len(self._article_posts(post_webhook)), 1)
                self.assertIn('記事も公開しました', self._result_email(send_mail))
                detail.refresh_from_db()
                self.assertIsNotNone(detail.article_published_notified_at)
                for repeated_endpoint in ('lt_application_review', 'lt_application_approve'):
                    self._post(detail, repeated_endpoint)
                self.assertEqual(send_mail.call_count, 2)
                self.assertEqual(len(self._article_posts(post_webhook)), 1)
                send_mail.reset_mock()
                post_webhook.reset_mock()

    def test_ng_or_empty_article_has_no_publication_notification_or_link(self, send_mail, post_webhook):
        """記事化 NG・記事なしでは、承認メールに記事リンクを載せず記事の通知も送らない。"""
        for endpoint in ('lt_application_review', 'lt_application_approve'):
            for changes in ({'article_consent': EventDetail.ArticleConsent.NG}, {'h1': '', 'contents': ''}):
                for notified_at in (None, timezone.now()):
                    with self.subTest(endpoint=endpoint, changes=changes, notified=bool(notified_at)):
                        detail = self._detail(article_published_notified_at=notified_at, **changes)
                        self.assertEqual(self._post(detail, endpoint).status_code, 302)
                        self.assertEqual(self._article_posts(post_webhook), [])
                        send_mail.assert_called_once()
                        html = self._result_email(send_mail)
                        self.assertNotIn('記事も公開しました', html)
                        self.assertNotIn(reverse('event:detail', kwargs={'pk': detail.pk}), html)
                        detail.refresh_from_db()
                        self.assertEqual(detail.article_published_notified_at, notified_at)
                        send_mail.reset_mock()
                        post_webhook.reset_mock()

    def test_rejection_does_not_notify_article(self, send_mail, post_webhook):
        """両方の却下経路で、記事があっても公開通知と記事リンクを出さない。"""
        for endpoint in ('lt_application_review', 'lt_application_reject'):
            with self.subTest(endpoint=endpoint):
                detail = self._detail()
                self.assertEqual(self._post(detail, endpoint, action='reject').status_code, 302)
                self.assertEqual(self._article_posts(post_webhook), [])
                send_mail.assert_called_once()
                self.assertNotIn('記事も公開しました', send_mail.call_args.kwargs['html_message'])
                detail.refresh_from_db()
                self.assertEqual(detail.status, 'rejected')
                self.assertIsNone(detail.article_published_notified_at)
                send_mail.reset_mock()
                post_webhook.reset_mock()

    def test_changes_before_notification_are_read_again(self, send_mail, post_webhook):
        """承認と同時に NG・削除・却下・記事なしになった場合は最新行で判断する。"""
        for changes in (
            {'article_consent': EventDetail.ArticleConsent.NG},
            {'deleted_at': timezone.now()},
            {'status': 'rejected'},
            {'h1': '', 'contents': ''},
        ):
            for notified_at in (None, timezone.now()):
                with self.subTest(changes=changes, notified=bool(notified_at)):
                    detail = self._detail(status='approved', article_published_notified_at=notified_at)
                    EventDetail.all_objects.filter(pk=detail.pk).update(**changes)
                    notify_article_on_approval(detail.pk)
                    send_mail.assert_not_called()
                    post_webhook.assert_not_called()
                    notify_applicant_of_result(detail)
                    self.assertNotIn('記事も公開しました', send_mail.call_args.kwargs['html_message'])
                    send_mail.reset_mock()
                    post_webhook.reset_mock()

    def test_notification_failure_does_not_cancel_approval(self, send_mail, post_webhook):
        """記事通知の例外をログに残し、両経路とも承認を完了する。"""
        for endpoint in ('lt_application_review', 'lt_application_approve'):
            with self.subTest(endpoint=endpoint):
                detail = self._detail(article_published_notified_at=timezone.now())
                with patch(
                    'event.services.article_generation._send_discord_notification_for_article',
                    side_effect=RuntimeError('notification failed'),
                ), self.assertLogs('event.services.article_generation', level='ERROR'):
                    self.assertEqual(self._post(detail, endpoint).status_code, 302)
                detail.refresh_from_db()
                self.assertEqual(detail.status, 'approved')

    def test_list_approval_rechecks_status_under_lock(self, send_mail, post_webhook):
        """一覧の読み込み後に処理済みになった申請は再承認・再通知しない。"""
        detail = self._detail(article_published_notified_at=timezone.now())
        EventDetail.objects.filter(pk=detail.pk).update(status='approved')
        with patch('event.views.lt_application.get_object_or_404', return_value=detail):
            self.assertEqual(self._post(detail, 'lt_application_approve').status_code, 302)
        send_mail.assert_not_called()
        post_webhook.assert_not_called()
