"""Discord の審査ボタンの権限・状態遷移と既存の後処理を検証する。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier
from unittest.mock import patch

from allauth.socialaccount.models import SocialAccount
from django.core.cache import cache
from django.db import close_old_connections, connection, connections
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from rest_framework.test import APIClient

from community.models import CommunityMember
from event.models import Event, EventDetail
from tests.factories import make_community, make_discord_linked_user, make_event, make_event_detail
from twitter.models import TweetQueue
from user_account.models import APIKey


class EventDetailReviewAPITest(TestCase):
    discord_id = '123456789012345678'
    staff_discord_id = '234567890123456789'

    @classmethod
    def setUpTestData(cls):
        cls.admin = make_discord_linked_user(
            user_name='review_admin', email='review_admin@example.com', is_superuser=True, is_staff=True,
        )
        cls.owner = make_discord_linked_user(
            user_name='review_owner', email='review_owner@example.com', discord_uid=cls.discord_id,
        )
        cls.staff = make_discord_linked_user(
            user_name='review_staff', email='review_staff@example.com', discord_uid=cls.staff_discord_id,
        )
        cls.outsider = make_discord_linked_user(
            user_name='review_outsider', email='review_outsider@example.com', discord_uid='345678901234567890',
            is_staff=True,
        )
        cls.community = make_community(owner=cls.owner)
        CommunityMember.objects.create(community=cls.community, user=cls.staff, role=CommunityMember.Role.STAFF)
        make_community(owner=cls.outsider, name='別の集会')
        cls.event = make_event(cls.community)
        cls.api_key, cls.raw_key = APIKey.create_with_raw_key(user=cls.admin, name='申請の審査')

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client = APIClient()
        self.client.credentials(HTTP_X_API_KEY=self.raw_key)
        self.detail = make_event_detail(self.event, source='discord_bot')
        self.url = reverse('event-detail-api-review', kwargs={'pk': self.detail.pk})
        self.notify_patch = patch('event.notifications.notify_applicant_of_result')
        self.notify = self.notify_patch.start()
        self.addCleanup(self.notify_patch.stop)

    def _post(self, decision='approved', discord_id=None):
        return self.client.post(self.url, {
            'decision': decision, 'discord_id': discord_id or self.discord_id,
        }, format='json')

    def _assert_pending(self):
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.status, 'pending')
        self.notify.assert_not_called()

    def test_owner_can_approve_and_notification_and_tweet_queue_run_once(self):
        response = self._post()

        self.assertEqual(self.url, f'/api/v1/event-details/{self.detail.pk}/review/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'id': self.detail.pk, 'status': 'approved'})
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.status, 'approved')
        self.notify.assert_called_once()
        self.assertEqual(self.notify.call_args.args[0].pk, self.detail.pk)
        self.assertEqual(self._post().status_code, 409)
        self.notify.assert_called_once()
        self.assertEqual(TweetQueue.objects.filter(event_detail=self.detail, tweet_type='lt').count(), 1)

    def test_staff_can_reject_without_a_reason(self):
        response = self._post('rejected', self.staff_discord_id)

        self.assertEqual(response.status_code, 200)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.status, 'rejected')
        self.assertEqual(self.detail.rejection_reason, '')
        self.notify.assert_called_once()
        self.assertFalse(TweetQueue.objects.filter(event_detail=self.detail).exists())

    def test_superuser_bearer_key_is_supported(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.raw_key}')
        self.assertEqual(self._post().status_code, 200)

    def test_non_superuser_keys_are_forbidden(self):
        for user in (self.owner, self.staff, self.outsider):
            with self.subTest(user=user.pk):
                _, raw_key = APIKey.create_with_raw_key(user=user, name='審査の権限確認')
                self.client.credentials(HTTP_X_API_KEY=raw_key)
                self.assertEqual(self._post().status_code, 403)
        self._assert_pending()

    def test_superuser_read_only_key_is_forbidden(self):
        key, raw_key = APIKey.create_with_raw_key(user=self.admin, name='参照専用')
        key.scope = APIKey.SCOPE_READ
        key.save(update_fields=['scope'])
        self.client.credentials(HTTP_X_API_KEY=raw_key)
        self.assertEqual(self._post().status_code, 403)
        self._assert_pending()

    def test_session_alone_and_missing_or_invalid_keys_are_forbidden(self):
        self.client.credentials()
        self.assertEqual(self._post().status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self._post().status_code, 403)
        self.client.credentials(HTTP_X_API_KEY='invalid-key')
        self.assertEqual(self._post().status_code, 403)
        self._assert_pending()

    def test_other_community_manager_unknown_and_unlinked_ids_are_forbidden(self):
        SocialAccount.objects.create(user=self.owner, provider='github', uid='456789012345678901')
        expected = self._post(discord_id='999999999999999999').json()
        for uid in ('345678901234567890', '456789012345678901', '999999999999999999'):
            for decision in ('approved', 'rejected'):
                with self.subTest(discord_id=uid, decision=decision):
                    response = self._post(decision, uid)
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.json(), expected)
        self._assert_pending()

    def test_discord_user_has_no_superuser_bypass(self):
        SocialAccount.objects.filter(user=self.admin, provider='discord').update(uid='567890123456789012')
        self.assertEqual(self._post(discord_id='567890123456789012').status_code, 403)
        self._assert_pending()

    def test_inactive_owner_and_staff_get_same_403_as_unlinked_user(self):
        expected = self._post(discord_id='999999999999999999').json()
        for user, uid in ((self.owner, self.discord_id), (self.staff, self.staff_discord_id)):
            user.is_active = False
            user.save(update_fields=['is_active'])
            for previous in ('pending', 'approved', 'rejected'):
                EventDetail.objects.filter(pk=self.detail.pk).update(status=previous)
                for decision in ('approved', 'rejected'):
                    with self.subTest(user=user.pk, previous=previous, decision=decision):
                        response = self._post(decision, uid)
                        self.assertEqual(response.status_code, 403)
                        self.assertEqual(response.json(), expected)
                        self.detail.refresh_from_db()
                        self.assertEqual(self.detail.status, previous)
        self.notify.assert_not_called()
        self.assertFalse(TweetQueue.objects.filter(event_detail=self.detail).exists())

    def test_invalid_payloads_return_400_without_changes(self):
        valid = {'decision': 'approved', 'discord_id': self.discord_id}
        invalid_ids = [None, 123456789012345678, True, [], {}, '', 'abc', '1' * 16, '1' * 21,
                       '１２３４５６７８９０１２３４５６７８', '١' * 18, f' {self.discord_id}',
                       f'{self.discord_id}\n', f'+{self.discord_id}']
        payloads = [{}, {'decision': 'approved'}, {'discord_id': self.discord_id}, [], 'approved', None]
        payloads += [{**valid, 'discord_id': value} for value in invalid_ids]
        payloads += [{**valid, 'decision': value} for value in ('approve', 'reject', '', None, [], {})]
        for payload in payloads:
            with self.subTest(payload=payload):
                response = self.client.post(self.url, payload, format='json')
                self.assertEqual(response.status_code, 400, response.data)
        self._assert_pending()

    def test_permission_is_checked_before_body_validation(self):
        _, raw_key = APIKey.create_with_raw_key(user=self.owner, name='検証順')
        self.client.credentials(HTTP_X_API_KEY=raw_key)
        self.assertEqual(self.client.post(self.url, {}, format='json').status_code, 403)

    def test_processed_applications_return_409_for_both_decisions(self):
        for previous in ('approved', 'rejected'):
            EventDetail.objects.filter(pk=self.detail.pk).update(status=previous)
            for decision in ('approved', 'rejected'):
                with self.subTest(previous=previous, decision=decision):
                    response = self._post(decision)
                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(response.data['code'], 'already_reviewed')
                    self.detail.refresh_from_db()
                    self.assertEqual(self.detail.status, previous)
        self.notify.assert_not_called()

    def test_non_manager_still_gets_403_for_processed_application(self):
        EventDetail.objects.filter(pk=self.detail.pk).update(status='approved')
        self.assertEqual(self._post(discord_id='345678901234567890').status_code, 403)

    def test_missing_and_deleted_applications_return_404(self):
        self.url = reverse('event-detail-api-review', kwargs={'pk': self.detail.pk + 1000})
        self.assertEqual(self._post().status_code, 404)
        self.detail.delete()
        self.url = reverse('event-detail-api-review', kwargs={'pk': self.detail.pk})
        self.assertEqual(self._post().status_code, 404)
        self.notify.assert_not_called()

    def test_status_is_rechecked_after_lock_instead_of_using_stale_instance(self):
        def processed_after_read(*args, **kwargs):
            stale = get_object_or_404(*args, **kwargs)
            EventDetail.objects.filter(pk=self.detail.pk).update(status='approved')
            return stale

        with patch('api_v1.views.get_object_or_404', side_effect=processed_after_read):
            self.assertEqual(self._post('rejected').status_code, 409)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.status, 'approved')
        self.notify.assert_not_called()

    def test_application_deleted_between_initial_read_and_lock_returns_404(self):
        def deleted_after_read(*args, **kwargs):
            stale = get_object_or_404(*args, **kwargs)
            EventDetail.objects.filter(pk=self.detail.pk).delete()
            return stale

        for decision in ('approved', 'rejected'):
            with self.subTest(decision=decision):
                self.detail = make_event_detail(self.event)
                self.url = reverse('event-detail-api-review', args=[self.detail.pk])
                with patch('api_v1.views.get_object_or_404', side_effect=deleted_after_read):
                    self.assertEqual(self._post(decision).status_code, 404)
                self.assertFalse(EventDetail.objects.filter(pk=self.detail.pk).exists())
                self.assertFalse(TweetQueue.objects.filter(event_detail=self.detail).exists())
        self.notify.assert_not_called()

    def test_parent_event_deleted_between_initial_read_and_lock_returns_404(self):
        def event_deleted_after_read(*args, **kwargs):
            stale = get_object_or_404(*args, **kwargs)
            Event.objects.filter(pk=stale.event_id).delete()
            return stale

        for offset, decision in enumerate(('approved', 'rejected'), start=30):
            with self.subTest(decision=decision):
                self.event = make_event(self.community, event_date=date.today() + timedelta(days=offset))
                self.detail = make_event_detail(self.event)
                self.url = reverse('event-detail-api-review', args=[self.detail.pk])
                with patch('api_v1.views.get_object_or_404', side_effect=event_deleted_after_read):
                    self.assertEqual(self._post(decision).status_code, 404)
                self.assertFalse(EventDetail.objects.filter(pk=self.detail.pk).exists())
        self.notify.assert_not_called()

    def test_audit_log_identifies_discord_reviewer_and_api_key_without_secret(self):
        for decision in ('approved', 'rejected'):
            with self.subTest(decision=decision):
                EventDetail.objects.filter(pk=self.detail.pk).update(status='pending')
                with self.assertLogs('event.services.application_review', level='INFO') as logs:
                    self.assertEqual(self._post(decision, self.staff_discord_id).status_code, 200)
                self.assertEqual(len(logs.records), 1)
                record = logs.records[0]
                self.assertEqual(record.getMessage(), 'event_detail_reviewed')
                self.assertEqual(record.event_detail_id, self.detail.pk)
                self.assertEqual(record.community_id, self.community.pk)
                self.assertEqual(record.decision, decision)
                self.assertEqual(record.reviewer_id, self.staff.pk)
                self.assertNotEqual(record.reviewer_id, self.admin.pk)
                self.assertEqual(record.api_key_id, self.api_key.pk)
                self.assertNotIn(self.raw_key, repr(record.__dict__))
                self.assertNotIn(self.api_key.key, repr(record.__dict__))

    def test_both_decisions_take_detail_lock_in_transaction(self):
        original = QuerySet.select_for_update
        locked_models = []

        def check_lock(queryset, *args, **kwargs):
            self.assertTrue(connection.in_atomic_block)
            locked_models.append(queryset.model)
            return original(queryset, *args, **kwargs)

        for decision in ('approved', 'rejected'):
            EventDetail.objects.filter(pk=self.detail.pk).update(status='pending')
            locked_models.clear()
            with patch.object(QuerySet, 'select_for_update', autospec=True, side_effect=check_lock):
                self.assertEqual(self._post(decision).status_code, 200)
            self.assertIn(EventDetail, locked_models)

    def test_web_and_api_reviews_share_the_same_transition(self):
        self.client.force_login(self.owner)
        for endpoint in ('lt_application_approve', 'lt_application_reject', 'lt_application_review'):
            with self.subTest(endpoint=endpoint):
                detail = make_event_detail(self.event)
                self.url = reverse('event-detail-api-review', kwargs={'pk': detail.pk})
                self.notify.reset_mock()
                data = {
                    'action': 'approve', 'rejection_reason': '対象外', 'event': self.event.pk,
                    'start_time': detail.start_time.strftime('%H:%M'), 'duration': detail.duration,
                }
                response = self.client.post(reverse(f'event:{endpoint}', args=[detail.pk]), data)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(self._post().status_code, 409)
                self.notify.assert_called_once()

    def test_failure_in_approval_link_rolls_back_status_and_signals(self):
        with patch('event.services.application_review.link_approved_application', side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self._post()
        self._assert_pending()
        self.assertFalse(TweetQueue.objects.filter(event_detail=self.detail).exists())

    def _assert_result_and_article_notifications_once(self, decision, *, has_article):
        # 結果通知も記事通知も実装を通し、外部への送信部分だけを差し替える。
        self.notify_patch.stop()
        EventDetail.objects.filter(pk=self.detail.pk).update(
            article_consent=EventDetail.ArticleConsent.OK,
            h1='発表の記事' if has_article else '', contents='記事の本文' if has_article else '',
            applicant=self.owner,
        )
        self.community.notification_webhook_url = 'https://discord.invalid/offline-test'
        self.community.save(update_fields=['notification_webhook_url'])
        result_text = '発表申請が承認されました' if decision == 'approved' else '発表申請が却下されました'
        article_text = '発表の記事を公開しました'
        article_count = int(decision == 'approved' and has_article)

        def assert_counts():
            subjects = [call.kwargs['subject'] for call in send_mail.call_args_list]
            titles = [call.args[1]['embeds'][0]['title'] for call in post_webhook.call_args_list]
            for messages in (subjects, titles):
                self.assertEqual(sum(result_text in message for message in messages), 1)
                self.assertEqual(sum(article_text in message for message in messages), article_count)
                self.assertEqual(len(messages), 1 + article_count)

        with (
            patch('event.notifications.send_mail', return_value=1) as send_mail,
            patch('event.notifications.post_discord_webhook') as post_webhook,
        ):
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self._post(decision).status_code, 200)
            assert_counts()
            for repeated_decision in ('approved', 'rejected'):
                with self.captureOnCommitCallbacks(execute=True):
                    self.assertEqual(self._post(repeated_decision).status_code, 409)
                assert_counts()
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.status, decision)
        self.assertEqual(self.detail.article_published_notified_at is not None, bool(article_count))

    def test_approval_publishes_prepared_article_and_notifies_only_once(self):
        self._assert_result_and_article_notifications_once('approved', has_article=True)

    def test_approval_without_article_sends_only_one_result_notification(self):
        self._assert_result_and_article_notifications_once('approved', has_article=False)

    def test_rejection_with_article_sends_only_one_result_notification(self):
        self._assert_result_and_article_notifications_once('rejected', has_article=True)

    def test_api_does_not_overwrite_pending_content_or_schedule(self):
        EventDetail.objects.filter(pk=self.detail.pk).update(theme='申請者の最新の編集', duration=15)
        response = self.client.post(self.url, {
            'decision': 'approved', 'discord_id': self.discord_id,
            'theme': '上書き', 'duration': 60, 'event': 0,
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.theme, '申請者の最新の編集')
        self.assertEqual(self.detail.duration, 15)
        self.assertEqual(self.detail.event_id, self.event.pk)


class EventDetailReviewConcurrencyTest(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_approve_and_reject_process_only_once(self):
        admin = make_discord_linked_user(is_superuser=True, user_name='concurrent_admin')
        owner = make_discord_linked_user(
            user_name='concurrent_owner', email='concurrent_owner@example.com', discord_uid='123456789012345678',
        )
        detail = make_event_detail(make_event(make_community(owner=owner)))
        _, raw_key = APIKey.create_with_raw_key(user=admin, name='同時審査')
        url = reverse('event-detail-api-review', args=[detail.pk])
        barrier = Barrier(2)

        def run(decision):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_X_API_KEY=raw_key)
                barrier.wait(timeout=10)
                return client.post(url, {'decision': decision, 'discord_id': '123456789012345678'}, format='json')
            finally:
                connections.close_all()

        with patch('event.notifications.notify_applicant_of_result') as notify:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(run, decision) for decision in ('approved', 'rejected')]
                responses = [future.result(timeout=30) for future in futures]
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        notify.assert_called_once()
        detail.refresh_from_db()
        winner = next(response for response in responses if response.status_code == 200)
        self.assertEqual(detail.status, winner.data['status'])
