"""画面と API に共通する審査サービスの権限と監査情報。"""

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
