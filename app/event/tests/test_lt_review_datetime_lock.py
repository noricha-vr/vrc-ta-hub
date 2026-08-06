"""発表申請の承認画面が Vket コラボの日時ロックを迂回しないことのテスト。

承認画面の日時調整は api_v1.perform_update / EventDetailForm と同じロック規約を
共有する必要がある（共有しないと3つ目の迂回経路になる）。
"""

from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from tests.factories import make_community, make_event, make_event_detail
from user_account.tests.utils import create_discord_linked_user
from vket.models import VketCollaboration, VketParticipation


class LTReviewDatetimeLockTest(TestCase):
    """コラボ本体イベントに紐づく申請の日時変更を主催者に許さない"""

    def setUp(self):
        self.client = Client()
        self.owner = create_discord_linked_user(
            user_name='LockOwner',
            email='lock_owner@example.com',
            password='ownerpass123',
        )
        self.applicant = create_discord_linked_user(
            user_name='LockApplicant',
            email='lock_applicant@example.com',
            password='applicantpass123',
        )
        self.community = make_community(owner=self.owner)

        today = timezone.localdate()
        self.collab_event = make_event(
            self.community, event_date=today + timedelta(days=1)
        )
        self.free_event = make_event(
            self.community, event_date=today + timedelta(days=2)
        )
        collaboration = VketCollaboration.objects.create(
            slug='lt-review-lock-test',
            name='Vket LT Review Lock Test',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today,
            lt_deadline=today + timedelta(days=3),
        )
        VketParticipation.objects.create(
            collaboration=collaboration,
            community=self.community,
            published_event=self.collab_event,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
        )

        self.application = make_event_detail(
            self.collab_event,
            applicant=self.applicant,
            theme='ロック確認テーマ',
            speaker='Lock Speaker',
            duration=15,
            status='pending',
        )
        self.url = reverse(
            'event:lt_application_review', kwargs={'pk': self.application.pk}
        )

    def _post_data(self, **overrides):
        data = {
            'action': 'approve',
            'event': self.application.event_id,
            'start_time': self.application.start_time.strftime('%H:%M'),
            'duration': self.application.duration,
            'rejection_reason': '',
        }
        data.update(overrides)
        return data

    def test_start_time_change_on_locked_event_is_rejected(self):
        """ロック中イベントでは開始時刻を変更できない"""
        self.client.force_login(self.owner)

        response = self.client.post(self.url, self._post_data(start_time='23:45'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Vketコラボ期間中のため運営のみ変更できます。')
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, 'pending')

    def test_duration_change_on_locked_event_is_rejected(self):
        """ロック中イベントでは持ち時間を変更できない"""
        self.client.force_login(self.owner)

        response = self.client.post(self.url, self._post_data(duration=55))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Vketコラボ期間中のため運営のみ変更できます。')
        self.application.refresh_from_db()
        self.assertEqual(self.application.duration, 15)

    def test_moving_out_of_locked_event_is_rejected(self):
        """ロック中イベントから未ロックイベントへ持ち出せない"""
        self.client.force_login(self.owner)

        response = self.client.post(self.url, self._post_data(event=self.free_event.pk))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Vketコラボ期間中のため運営のみ変更できます。')
        self.application.refresh_from_db()
        self.assertEqual(self.application.event_id, self.collab_event.pk)

    def test_approve_without_schedule_change_is_allowed(self):
        """ロック中でも日時を変えない承認は通る"""
        self.client.force_login(self.owner)

        with self.settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'):
            response = self.client.post(self.url, self._post_data())

        self.assertEqual(response.status_code, 302)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, 'approved')

    def test_superuser_can_change_schedule_on_locked_event(self):
        """運営（superuser）はロック中でも日時を変更できる"""
        # can_edit は集会メンバーシップで判定するため、運営でも所属が要る
        self.owner.is_superuser = True
        self.owner.is_staff = True
        self.owner.save(update_fields=['is_superuser', 'is_staff'])
        self.client.force_login(self.owner)

        with self.settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'):
            response = self.client.post(self.url, self._post_data(start_time='23:45'))

        self.assertEqual(response.status_code, 302)
        self.application.refresh_from_db()
        self.assertEqual(self.application.start_time.strftime('%H:%M'), '23:45')
