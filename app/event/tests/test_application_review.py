"""画面と API に共通する審査サービスの権限と監査情報。"""

from datetime import time, timedelta
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from community.models import CommunityMember
from event.services.application_review import review_application
from tests.factories import make_community, make_discord_linked_user, make_event, make_event_detail
from twitter.models import TweetQueue


class ApplicationReviewServiceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_discord_linked_user()
        cls.staff = make_discord_linked_user(user_name='staff', email='staff@example.com')
        cls.community = make_community(owner=cls.owner)
        CommunityMember.objects.create(community=cls.community, user=cls.staff, role=CommunityMember.Role.STAFF)
        cls.event = make_event(cls.community)

    def test_auto_start_time_is_decided_after_lock_for_each_approval(self):
        """開催日だけ変えた承認を同じ回へ続けて行っても、枠が重ならない。

        フォームの検証時点では両方とも空の回に見えるため、計算は保存時に行う。
        """
        self.community.lt_start_offset_minutes = 15
        self.community.save(update_fields=['lt_start_offset_minutes'])
        new_event = make_event(self.community, event_date=self.event.date + timedelta(days=14))
        first = make_event_detail(self.event, start_time=time(22, 45))
        second = make_event_detail(self.event, start_time=time(23, 15))

        with patch('event.notifications.notify_applicant_of_result'):
            for detail in (first, second):
                review_application(
                    detail, 'approved', reviewer=self.owner,
                    event=new_event, start_time=None, duration=30,
                )

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.event_id, first.start_time), (new_event.pk, time(22, 15)))
        self.assertEqual((second.event_id, second.start_time), (new_event.pk, time(22, 45)))

    def test_inactive_managers_cannot_review_through_shared_service(self):
        detail = make_event_detail(self.event)
        with patch('event.notifications.notify_applicant_of_result') as notify:
            for reviewer in (self.owner, self.staff):
                reviewer.is_active = False
                reviewer.save(update_fields=['is_active'])
                for decision in ('approved', 'rejected'):
                    with self.subTest(reviewer=reviewer.pk, decision=decision):
                        with self.assertRaisesMessage(PermissionDenied, 'この申請を確認する権限がありません。'):
                            review_application(detail, decision, reviewer=reviewer)
                        detail.refresh_from_db()
                        self.assertEqual(detail.status, 'pending')
            notify.assert_not_called()
        self.assertFalse(TweetQueue.objects.filter(event_detail=detail).exists())

    def test_web_review_audit_log_records_reviewer_without_api_key(self):
        self.client.force_login(self.owner)
        for endpoint, decision in (('lt_application_approve', 'approved'), ('lt_application_reject', 'rejected')):
            with self.subTest(endpoint=endpoint):
                detail = make_event_detail(self.event)
                with (
                    patch('event.notifications.notify_applicant_of_result'),
                    self.assertLogs('event.services.application_review', level='INFO') as logs,
                ):
                    response = self.client.post(
                        reverse(f'event:{endpoint}', args=[detail.pk]), {'rejection_reason': '対象外'},
                    )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(len(logs.records), 1)
                record = logs.records[0]
                self.assertEqual(record.reviewer_id, self.owner.pk)
                self.assertIsNone(record.api_key_id)
                self.assertEqual(record.decision, decision)
                self.assertEqual(record.event_detail_id, detail.pk)

    def test_web_review_returns_404_when_parent_event_is_deleted_after_initial_read(self):
        from django.shortcuts import get_object_or_404

        from event.models import Event

        def event_deleted_after_read(*args, **kwargs):
            stale = get_object_or_404(*args, **kwargs)
            Event.objects.filter(pk=stale.event_id).delete()
            return stale

        self.client.force_login(self.owner)
        for offset, endpoint in enumerate(
            ('lt_application_review', 'lt_application_approve', 'lt_application_reject'), start=30,
        ):
            with self.subTest(endpoint=endpoint):
                event = make_event(self.community, event_date=self.event.date + timedelta(days=offset))
                detail = make_event_detail(event)
                with (
                    patch('event.views.lt_application.get_object_or_404', side_effect=event_deleted_after_read),
                    patch('event.notifications.notify_applicant_of_result') as notify,
                ):
                    response = self.client.post(
                        reverse(f'event:{endpoint}', args=[detail.pk]), {'rejection_reason': '対象外'},
                    )
                # 審査画面は削除済みの開催日をフォームの検証で弾いて再表示する。承認・却下は 404。
                self.assertIn(response.status_code, (200, 404) if endpoint == 'lt_application_review' else (404,))
                notify.assert_not_called()
                self.assertFalse(TweetQueue.objects.filter(event_detail_id=detail.pk).exists())
