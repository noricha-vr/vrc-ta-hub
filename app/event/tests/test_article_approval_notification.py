"""承認で公開になった記事の通知を、審査ページと一覧の両方で確かめる。"""
from unittest.mock import patch

from django.db import transaction
from django.db.models import QuerySet
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from event.models import EventDetail
from event.notifications import notify_applicant_of_result
from event.services.article_generation import _notify_first_time, schedule_article_notification_on_approval
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

    def _post(self, detail, endpoint, action='approve', execute_callbacks=True):
        data = {
            'action': action,
            'event': detail.event_id,
            'start_time': detail.start_time.strftime('%H:%M'),
            'duration': detail.duration,
            'rejection_reason': '集会の趣旨と合いません' if action == 'reject' else '',
        }
        with self.captureOnCommitCallbacks(execute=execute_callbacks):
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
                self.assertGreater(detail.article_published_notified_at, notified_at)

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
        for endpoint in ('lt_application_review', 'lt_application_approve'):
            for changes in (
                {'article_consent': EventDetail.ArticleConsent.NG},
                {'deleted_at': timezone.now()},
                {'status': 'rejected'},
                {'h1': '', 'contents': ''},
            ):
                for notified_at in (None, timezone.now()):
                    with self.subTest(endpoint=endpoint, changes=changes, notified=bool(notified_at)):
                        detail = self._detail(article_published_notified_at=notified_at)
                        with self.captureOnCommitCallbacks(execute=False) as callbacks:
                            self.assertEqual(self._post(detail, endpoint, execute_callbacks=False).status_code, 302)
                        self.assertTrue(callbacks)
                        send_mail.reset_mock()
                        post_webhook.reset_mock()
                        EventDetail.all_objects.filter(pk=detail.pk).update(**changes)

                        for callback in callbacks:
                            callback()

                        send_mail.assert_not_called()
                        post_webhook.assert_not_called()
                        detail.refresh_from_db()
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
                    'event.services.article_generation.send_discord_notification_for_article',
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

    def test_generation_after_approval_does_not_send_again(self, send_mail, post_webhook):
        """承認側が通知日時を取って送信済みなら、生成側はメールも Discord も重ねない。"""
        for endpoint in ('lt_application_review', 'lt_application_approve'):
            with self.subTest(endpoint=endpoint):
                detail = self._detail()
                self.assertEqual(self._post(detail, endpoint).status_code, 302)
                send_mail.reset_mock()

                _notify_first_time(detail.pk)

                send_mail.assert_not_called()
                self.assertEqual(len(self._article_posts(post_webhook)), 1)
                post_webhook.reset_mock()

    def test_approval_claim_blocks_generation_before_commit_callback(self, send_mail, post_webhook):
        """承認トランザクションで取った通知日時は、コミット後の送信前にも生成側が取れない。"""
        for endpoint in ('lt_application_review', 'lt_application_approve'):
            with self.subTest(endpoint=endpoint):
                detail = self._detail()
                with self.captureOnCommitCallbacks(execute=False) as callbacks:
                    self.assertEqual(self._post(detail, endpoint, execute_callbacks=False).status_code, 302)
                self.assertTrue(callbacks)
                detail.refresh_from_db()
                self.assertEqual(detail.status, 'approved')
                self.assertIsNotNone(detail.article_published_notified_at)
                send_mail.reset_mock()
                post_webhook.reset_mock()

                _notify_first_time(detail.pk)

                send_mail.assert_not_called()
                post_webhook.assert_not_called()
                for callback in callbacks:
                    callback()
                send_mail.assert_called_once()
                self.assertIn('公開しました', send_mail.call_args.kwargs['subject'])
                self.assertEqual(len(self._article_posts(post_webhook)), 1)
                send_mail.reset_mock()
                post_webhook.reset_mock()

    def test_generation_cannot_claim_inside_approval_transaction(self, send_mail, post_webhook):
        """承認側が担当を取った後、status の保存前でも生成側の取得は失敗する。"""
        for endpoint in ('lt_application_review', 'lt_application_approve'):
            with self.subTest(endpoint=endpoint):
                detail = self._detail()

                def claim_then_generate(locked):
                    self.assertTrue(transaction.get_connection().in_atomic_block)
                    self.assertEqual(locked.status, 'pending')
                    schedule_article_notification_on_approval(locked)
                    current = EventDetail.all_objects.get(pk=locked.pk)
                    self.assertEqual(current.status, 'pending')
                    self.assertIsNotNone(current.article_published_notified_at)
                    _notify_first_time(locked.pk)
                    send_mail.assert_not_called()
                    post_webhook.assert_not_called()

                with patch(
                    'event.services.article_generation.schedule_article_notification_on_approval',
                    side_effect=claim_then_generate,
                ):
                    self.assertEqual(self._post(detail, endpoint).status_code, 302)
                self.assertEqual(send_mail.call_count, 2)
                self.assertEqual(len(self._article_posts(post_webhook)), 1)
                send_mail.reset_mock()
                post_webhook.reset_mock()

    def test_generation_uses_status_at_claim(self, send_mail, post_webhook):
        """生成側が pending を読んでも、取得時に approved なら公開メールと Discord を送る。"""
        detail = self._detail()
        original_update = QuerySet.update

        def approve_before_claim(queryset, **changes):
            if changes.get('article_published_notified_at') is not None:
                original_update(EventDetail.all_objects.filter(pk=detail.pk), status='approved')
            return original_update(queryset, **changes)

        with patch.object(QuerySet, 'update', autospec=True, side_effect=approve_before_claim):
            _notify_first_time(detail.pk)

        send_mail.assert_called_once()
        self.assertIn('公開しました', send_mail.call_args.kwargs['subject'])
        self.assertEqual(len(self._article_posts(post_webhook)), 1)

    def test_approval_takes_over_claim_before_generation_email_fails(self, send_mail, post_webhook):
        """承認前の作成メールが遅れて失敗しても、承認側の通知日時と Discord の 1 回を保つ。"""
        for endpoint in ('lt_application_review', 'lt_application_approve'):
            with self.subTest(endpoint=endpoint):
                detail = self._detail()
                claimed_times = []

                def approve_during_email(**kwargs):
                    if '発表の記事を作成しました' in kwargs['subject']:
                        detail.refresh_from_db()
                        claimed_times.append(detail.article_published_notified_at)
                        self.assertEqual(self._post(detail, endpoint).status_code, 302)
                        detail.refresh_from_db()
                        claimed_times.append(detail.article_published_notified_at)
                        self.assertEqual(len(self._article_posts(post_webhook)), 1)
                        return 0
                    return 1

                send_mail.side_effect = approve_during_email
                _notify_first_time(detail.pk)

                self.assertEqual(len(claimed_times), 2)
                self.assertIsNotNone(claimed_times[0])
                self.assertGreater(claimed_times[1], claimed_times[0])
                detail.refresh_from_db()
                self.assertEqual(detail.article_published_notified_at, claimed_times[1])
                self.assertIn('記事も公開しました', self._result_email(send_mail))
                self.assertEqual(send_mail.call_count, 2)
                send_mail.side_effect = None
                send_mail.reset_mock()
                EventDetail.all_objects.filter(pk=detail.pk).update(h1='作り直した記事', contents='新しい本文')

                _notify_first_time(detail.pk)

                send_mail.assert_not_called()
                self.assertEqual(len(self._article_posts(post_webhook)), 1)
                post_webhook.reset_mock()

    def test_generation_rechecks_article_after_claim(self, send_mail, post_webhook):
        """日時取得後に記事が空・NG・却下・削除になったら、日時を戻して送らない。"""
        for changes in (
            {'h1': '', 'contents': ''},
            {'article_consent': EventDetail.ArticleConsent.NG},
            {'status': 'rejected'},
            {'deleted_at': timezone.now()},
        ):
            with self.subTest(changes=changes):
                detail = self._detail()
                original_update = QuerySet.update

                def change_after_claim(queryset, **values):
                    updated = original_update(queryset, **values)
                    if values.get('article_published_notified_at') is not None:
                        original_update(EventDetail.all_objects.filter(pk=detail.pk), **changes)
                    return updated

                with patch.object(QuerySet, 'update', autospec=True, side_effect=change_after_claim):
                    _notify_first_time(detail.pk)

                send_mail.assert_not_called()
                post_webhook.assert_not_called()
                detail.refresh_from_db()
                self.assertIsNone(detail.article_published_notified_at)

    def test_publication_email_rechecks_article_before_discord(self, send_mail, post_webhook):
        """公開メール送信中に NG・却下・削除・記事なしになったら Discord に流さない。"""
        for changes in (
            {'article_consent': EventDetail.ArticleConsent.NG},
            {'status': 'rejected'},
            {'deleted_at': timezone.now()},
            {'h1': '', 'contents': ''},
        ):
            with self.subTest(changes=changes):
                detail = self._detail(status='approved')

                def change_during_email(**kwargs):
                    EventDetail.all_objects.filter(pk=detail.pk).update(**changes)
                    return 1

                send_mail.side_effect = change_during_email
                _notify_first_time(detail.pk)

                send_mail.assert_called_once()
                self.assertIn('発表の記事を公開しました', send_mail.call_args.kwargs['subject'])
                post_webhook.assert_not_called()
                detail.refresh_from_db()
                self.assertIsNotNone(detail.article_published_notified_at)
                send_mail.side_effect = None
                send_mail.reset_mock()

    def test_publication_discord_uses_article_after_email(self, send_mail, post_webhook):
        """公開メールの送信中に本文が変わったら、Discord は読み直した記事で作る。"""
        detail = self._detail(status='approved')

        def change_during_email(**kwargs):
            EventDetail.all_objects.filter(pk=detail.pk).update(h1='メール送信中に直した記事')
            return 1

        send_mail.side_effect = change_during_email
        _notify_first_time(detail.pk)

        send_mail.assert_called_once()
        self.assertEqual(self._article_posts(post_webhook)[0]['description'], '**メール送信中に直した記事**')

    def test_rollback_discards_approval_notification_and_claim(self, send_mail, post_webhook):
        """承認がロールバックされた場合は通知日時もコールバックも残さない。"""
        detail = self._detail()
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with transaction.atomic():
                locked = EventDetail.objects.select_for_update().get(pk=detail.pk)
                schedule_article_notification_on_approval(locked)
                locked.status = 'approved'
                locked.save(update_fields=['status'])
                transaction.set_rollback(True)

        self.assertEqual(callbacks, [])
        detail.refresh_from_db()
        self.assertEqual(detail.status, 'pending')
        self.assertIsNone(detail.article_published_notified_at)
        send_mail.assert_not_called()
        post_webhook.assert_not_called()

    def test_discord_only_notification_uses_latest_article(self, send_mail, post_webhook):
        """作成メール済みの公開通知は、送信直前に読み直した本文で作る。"""
        detail = self._detail(article_published_notified_at=timezone.now())
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self._post(detail, 'lt_application_approve', execute_callbacks=False)
        EventDetail.all_objects.filter(pk=detail.pk).update(h1='送信前に直した記事')
        send_mail.reset_mock()
        post_webhook.reset_mock()

        for callback in callbacks:
            callback()

        send_mail.assert_not_called()
        self.assertEqual(self._article_posts(post_webhook)[0]['description'], '**送信前に直した記事**')

    def test_failed_approval_email_releases_only_its_own_claim(self, send_mail, post_webhook):
        """承認時のメール失敗は自分の通知日時だけを戻し、他の取得を消さない。"""
        for replaced in (False, True):
            with self.subTest(replaced=replaced):
                detail = self._detail()
                with self.captureOnCommitCallbacks(execute=False) as callbacks:
                    self._post(detail, 'lt_application_approve', execute_callbacks=False)
                detail.refresh_from_db()
                claimed_at = detail.article_published_notified_at
                self.assertIsNotNone(claimed_at)
                replaced_at = timezone.now()

                def fail_email(**kwargs):
                    if replaced:
                        EventDetail.all_objects.filter(pk=detail.pk).update(
                            article_published_notified_at=replaced_at,
                        )
                    return 0

                send_mail.reset_mock()
                post_webhook.reset_mock()
                send_mail.side_effect = fail_email
                for callback in callbacks:
                    callback()
                detail.refresh_from_db()
                self.assertEqual(detail.article_published_notified_at, replaced_at if replaced else None)
                post_webhook.assert_not_called()
                send_mail.side_effect = None
                send_mail.reset_mock()
