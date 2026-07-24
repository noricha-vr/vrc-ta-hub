"""EventDeleteViewの権限テスト"""
from datetime import date, time, timedelta
from unittest.mock import patch

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from community.models import Community, CommunityMember
from event.models import Event, EventOccurrenceTombstone, RecurrenceRule
from event.tests.tweet_generation import TweetGenerationPatchMixin
from vket.models import VketCollaboration, VketParticipation

User = get_user_model()

class EventDeleteViewPermissionTest(TweetGenerationPatchMixin, TestCase):
    """EventDeleteViewの権限チェックテスト"""

    def setUp(self):
        """テスト用データの準備"""
        self.client = Client()

        # ユーザー作成
        self.owner_user = User.objects.create_user(
            user_name='Owner User',
            email='owner@example.com',
            password='ownerpass123'
        )
        self.staff_user = User.objects.create_user(
            user_name='Staff User',
            email='staff@example.com',
            password='staffpass123'
        )
        self.other_user = User.objects.create_user(
            user_name='Other User',
            email='other@example.com',
            password='otherpass123'
        )

        # コミュニティ作成
        self.community = Community.objects.create(
            name='Test Community',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved'
        )

        # CommunityMemberを作成
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner_user,
            role=CommunityMember.Role.OWNER
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.staff_user,
            role=CommunityMember.Role.STAFF
        )

        # イベント作成
        self.event = Event.objects.create(
            community=self.community,
            date=date(2025, 2, 1),
            start_time=time(22, 0),
            duration=60,
            weekday='Sat'
        )

    def test_owner_can_delete_event(self):
        """主催者はイベントを削除できる"""
        self.client.login(username='Owner User', password='ownerpass123')

        url = reverse('event:delete', kwargs={'pk': self.event.pk})
        response = self.client.post(url)

        # リダイレクトされることを確認
        self.assertEqual(response.status_code, 302)

        # イベントが削除されたことを確認
        self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())
        self.assertTrue(
            EventOccurrenceTombstone.objects.filter(
                community=self.community,
                date=self.event.date,
                reason=EventOccurrenceTombstone.Reason.DELETED,
            ).exists()
        )

    def test_master_delete_records_cascade_occurrences(self):
        """親削除はCASCADEされる子開催回もtombstoneへ記録する"""
        rule = RecurrenceRule.objects.create(
            community=self.community,
            frequency='WEEKLY',
        )
        self.event.is_recurring_master = True
        self.event.recurrence_rule = rule
        self.event.save(update_fields=['is_recurring_master', 'recurrence_rule'])
        child_date = self.event.date + timedelta(days=7)
        child = Event.objects.create(
            community=self.community,
            date=child_date,
            start_time=self.event.start_time,
            duration=60,
            recurring_master=self.event,
        )
        self.client.login(username='Owner User', password='ownerpass123')

        response = self.client.post(
            reverse('event:delete', kwargs={'pk': self.event.pk})
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Event.objects.filter(pk=child.pk).exists())
        self.assertEqual(
            set(
                EventOccurrenceTombstone.objects.filter(
                    community=self.community,
                ).values_list('date', flat=True)
            ),
            {self.event.date, child_date},
        )

    def test_delete_subsequent_records_each_target_date(self):
        """以降も削除する場合は各開催日をtombstoneへ記録する"""
        subsequent_dates = [
            self.event.date + timedelta(days=7),
            self.event.date + timedelta(days=14),
        ]
        for target_date in subsequent_dates:
            Event.objects.create(
                community=self.community,
                date=target_date,
                start_time=self.event.start_time,
                duration=60,
            )
        self.client.login(username='Owner User', password='ownerpass123')

        self.client.post(
            reverse('event:delete', kwargs={'pk': self.event.pk}),
            {'delete_subsequent': 'on'},
        )

        self.assertEqual(
            set(
                EventOccurrenceTombstone.objects.filter(
                    community=self.community,
                ).values_list('date', flat=True)
            ),
            {self.event.date, *subsequent_dates},
        )

    @patch('event.views.crud.GoogleCalendarService')
    def test_google_delete_failure_keeps_database_delete(
        self,
        calendar_service_class,
    ):
        """Google削除失敗時もDB削除とtombstoneを維持する"""
        self.event.google_calendar_event_id = 'google-event-id'
        self.event.save(update_fields=['google_calendar_event_id'])
        calendar_service_class.return_value.delete_event.side_effect = RuntimeError(
            'calendar unavailable'
        )
        self.client.login(username='Owner User', password='ownerpass123')

        response = self.client.post(
            reverse('event:delete', kwargs={'pk': self.event.pk}),
            follow=True,
        )

        self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())
        self.assertTrue(
            EventOccurrenceTombstone.objects.filter(
                community=self.community,
                date=self.event.date,
            ).exists()
        )
        self.assertContains(response, 'Googleカレンダー削除に失敗しました')
        self.assertContains(response, '後続の同期で再反映します')

    @patch('event.views.crud.GoogleCalendarService')
    def test_partial_google_failure_does_not_restore_cascade(
        self,
        calendar_service_class,
    ):
        """Googleの一部失敗でも親子のDB削除を維持する"""
        rule = RecurrenceRule.objects.create(
            community=self.community,
            frequency='WEEKLY',
        )
        self.event.is_recurring_master = True
        self.event.recurrence_rule = rule
        self.event.google_calendar_event_id = 'google-master-id'
        self.event.save(
            update_fields=[
                'is_recurring_master',
                'recurrence_rule',
                'google_calendar_event_id',
            ]
        )
        child_date = self.event.date + timedelta(days=7)
        child = Event.objects.create(
            community=self.community,
            date=child_date,
            start_time=self.event.start_time,
            duration=60,
            recurring_master=self.event,
            google_calendar_event_id='google-child-id',
        )

        def delete_after_database_commit(event_id):
            self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())
            self.assertFalse(Event.objects.filter(pk=child.pk).exists())
            self.assertEqual(
                EventOccurrenceTombstone.objects.filter(
                    community=self.community,
                ).count(),
                2,
            )
            if event_id == 'google-child-id':
                raise RuntimeError('calendar unavailable')

        calendar_service_class.return_value.delete_event.side_effect = (
            delete_after_database_commit
        )
        self.client.login(username='Owner User', password='ownerpass123')

        response = self.client.post(
            reverse('event:delete', kwargs={'pk': self.event.pk}),
            follow=True,
        )

        self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())
        self.assertFalse(Event.objects.filter(pk=child.pk).exists())
        self.assertEqual(
            EventOccurrenceTombstone.objects.filter(
                community=self.community,
            ).count(),
            2,
        )
        self.assertEqual(
            calendar_service_class.return_value.delete_event.call_count,
            2,
        )
        self.assertContains(response, '1件のGoogleカレンダー削除に失敗しました')

    @patch('event.views.crud.GoogleCalendarService')
    def test_master_delete_rejects_locked_child_cascade(
        self,
        calendar_service_class,
    ):
        """Vketロック中の子があれば親子とも削除しない"""
        rule = RecurrenceRule.objects.create(
            community=self.community,
            frequency='WEEKLY',
        )
        self.event.is_recurring_master = True
        self.event.recurrence_rule = rule
        self.event.google_calendar_event_id = 'google-master-id'
        self.event.save(
            update_fields=[
                'is_recurring_master',
                'recurrence_rule',
                'google_calendar_event_id',
            ]
        )
        locked_date = date.today() + timedelta(days=1)
        child = Event.objects.create(
            community=self.community,
            date=locked_date,
            start_time=self.event.start_time,
            duration=60,
            recurring_master=self.event,
            google_calendar_event_id='google-child-id',
        )
        collaboration = VketCollaboration.objects.create(
            slug='locked-child-cascade',
            name='Vket子開催回ロック',
            period_start=locked_date,
            period_end=locked_date,
            registration_deadline=locked_date,
            lt_deadline=locked_date,
        )
        VketParticipation.objects.create(
            collaboration=collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
        )
        self.client.login(username='Owner User', password='ownerpass123')

        response = self.client.post(
            reverse('event:delete', kwargs={'pk': self.event.pk}),
            follow=True,
        )

        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())
        self.assertTrue(Event.objects.filter(pk=child.pk).exists())
        self.assertFalse(EventOccurrenceTombstone.objects.exists())
        calendar_service_class.return_value.delete_event.assert_not_called()
        self.assertContains(response, '親イベントを含む削除を中止しました')

    def test_staff_cannot_delete_event(self):
        """スタッフはイベントを削除できない"""
        self.client.login(username='Staff User', password='staffpass123')

        url = reverse('event:delete', kwargs={'pk': self.event.pk})
        response = self.client.post(url)

        # リダイレクトされることを確認
        self.assertEqual(response.status_code, 302)

        # イベントが削除されていないことを確認
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())

    def test_other_user_cannot_delete_event(self):
        """コミュニティ外のユーザーはイベントを削除できない"""
        self.client.login(username='Other User', password='otherpass123')

        url = reverse('event:delete', kwargs={'pk': self.event.pk})
        response = self.client.post(url)

        # リダイレクトされることを確認
        self.assertEqual(response.status_code, 302)

        # イベントが削除されていないことを確認
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())

    def test_anonymous_user_redirected_to_login(self):
        """未ログインユーザーはログインページにリダイレクトされる"""
        url = reverse('event:delete', kwargs={'pk': self.event.pk})
        response = self.client.post(url)

        # ログインページにリダイレクトされることを確認
        self.assertEqual(response.status_code, 302)
        self.assertIn('/account/login/', response.url)

        # イベントが削除されていないことを確認
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())


class EventDeleteViewMultipleOwnersTest(TweetGenerationPatchMixin, TestCase):
    """複数主催者がいる場合のEventDeleteViewテスト"""

    def setUp(self):
        """テスト用データの準備"""
        self.client = Client()

        # ユーザー作成
        self.owner1 = User.objects.create_user(
            user_name='Owner1 User',
            email='owner1@example.com',
            password='owner1pass123'
        )
        self.owner2 = User.objects.create_user(
            user_name='Owner2 User',
            email='owner2@example.com',
            password='owner2pass123'
        )

        # コミュニティ作成
        self.community = Community.objects.create(
            name='Multi Owner Community',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved'
        )

        # 複数の主催者を設定
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner1,
            role=CommunityMember.Role.OWNER
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner2,
            role=CommunityMember.Role.OWNER
        )

        # イベント作成
        self.event = Event.objects.create(
            community=self.community,
            date=date(2025, 2, 1),
            start_time=time(22, 0),
            duration=60,
            weekday='Sat'
        )

    def test_second_owner_can_delete_event(self):
        """2人目の主催者もイベントを削除できる"""
        self.client.login(username='Owner2 User', password='owner2pass123')

        url = reverse('event:delete', kwargs={'pk': self.event.pk})
        response = self.client.post(url)

        # リダイレクトされることを確認
        self.assertEqual(response.status_code, 302)

        # イベントが削除されたことを確認
        self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())
