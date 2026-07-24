"""EventUpdateView（開始時刻編集）のテスト。"""
from datetime import date, time, timedelta
from unittest import mock

from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from community.models import Community, CommunityMember
from event.models import Event, EventDetail
from event.tests.tweet_generation import TweetGenerationPatchMixin
from user_account.models import CustomUser


class EventUpdateViewBaseMixin(TweetGenerationPatchMixin):
    """テスト共通のセットアップ。"""

    def setUp(self):
        self.client = Client()

        self.owner = CustomUser.objects.create_user(
            user_name='Owner User', email='owner@example.com', password='ownerpass',
        )
        self.staff = CustomUser.objects.create_user(
            user_name='Staff User', email='staff@example.com', password='staffpass',
        )
        self.outsider = CustomUser.objects.create_user(
            user_name='Outsider User', email='out@example.com', password='outpass',
        )
        self.superuser = CustomUser.objects.create_superuser(
            user_name='Super User', email='super@example.com', password='superpass',
        )

        self.community = Community.objects.create(
            name='Test Community',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Sat'],
            frequency='Every week',
            organizers='Test',
            status='approved',
        )
        CommunityMember.objects.create(
            community=self.community, user=self.owner, role=CommunityMember.Role.OWNER,
        )
        CommunityMember.objects.create(
            community=self.community, user=self.staff, role=CommunityMember.Role.STAFF,
        )

        # 別コミュニティ
        self.other_community = Community.objects.create(
            name='Other Community',
            start_time=time(21, 0),
            duration=60,
            weekdays=['Sun'],
            frequency='Every week',
            organizers='Other',
            status='approved',
        )

        future = timezone.now().date() + timedelta(days=14)
        self.event = Event.objects.create(
            community=self.community,
            date=future,
            start_time=time(22, 0),
            duration=60,
            weekday='SAT',
        )
        self.url = reverse('event:update', kwargs={'pk': self.event.pk})


class EventUpdateViewPermissionTest(EventUpdateViewBaseMixin, TestCase):
    def test_anonymous_redirected_to_login(self):
        response = self.client.post(self.url, {'start_time': '21:00'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/account/login/', response.url)
        self.event.refresh_from_db()
        self.assertEqual(self.event.start_time, time(22, 0))

    def test_owner_can_update(self):
        self.client.login(username='Owner User', password='ownerpass')
        response = self.client.post(self.url, {'start_time': '21:00'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('event:my_list'))
        self.event.refresh_from_db()
        self.assertEqual(self.event.start_time, time(21, 0))

    def test_staff_can_update(self):
        self.client.login(username='Staff User', password='staffpass')
        response = self.client.post(self.url, {'start_time': '21:30'})
        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.start_time, time(21, 30))

    def test_outsider_cannot_update(self):
        self.client.login(username='Outsider User', password='outpass')
        response = self.client.post(self.url, {'start_time': '21:00'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('event:my_list'))
        self.event.refresh_from_db()
        self.assertEqual(self.event.start_time, time(22, 0))


class EventUpdateViewFormValidationTest(EventUpdateViewBaseMixin, TestCase):
    def test_unique_constraint_violation_shows_form_error(self):
        # 同 community/date で別 start_time のイベントを追加
        Event.objects.create(
            community=self.community,
            date=self.event.date,
            start_time=time(23, 0),
            duration=60,
            weekday='SAT',
        )
        self.client.login(username='Owner User', password='ownerpass')
        response = self.client.post(self.url, {'start_time': '23:00'})
        # フォーム再表示（200）またはリダイレクトを許容せず、DB 不変を主軸に確認
        self.event.refresh_from_db()
        self.assertEqual(self.event.start_time, time(22, 0))
        # クリーン側でエラー化するため 200 が返る
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '同じ日時にすでにイベントが登録されています。')

    def test_event_detail_start_times_shift_by_delta(self):
        # 開始 22:00 のイベント配下の発表を作成
        detail1 = EventDetail.objects.create(
            event=self.event, detail_type='LT', start_time=time(22, 15), duration=15,
        )
        detail2 = EventDetail.objects.create(
            event=self.event, detail_type='LT', start_time=time(22, 45), duration=15,
        )
        self.client.login(username='Owner User', password='ownerpass')
        response = self.client.post(self.url, {'start_time': '21:00'})
        self.assertEqual(response.status_code, 302)

        detail1.refresh_from_db()
        detail2.refresh_from_db()
        # 1 時間前倒し
        self.assertEqual(detail1.start_time, time(21, 15))
        self.assertEqual(detail2.start_time, time(21, 45))


class EventUpdateViewGoogleCalendarTest(EventUpdateViewBaseMixin, TestCase):
    def test_google_calendar_update_called_with_new_time(self):
        self.event.google_calendar_event_id = 'gcal-abc'
        self.event.save(update_fields=['google_calendar_event_id'])

        self.client.login(username='Owner User', password='ownerpass')
        with mock.patch('event.views.crud.GoogleCalendarService') as MockService:
            instance = MockService.return_value
            response = self.client.post(self.url, {'start_time': '20:00'})

        self.assertEqual(response.status_code, 302)
        MockService.assert_called_once()
        instance.update_event.assert_called_once()
        call_kwargs = instance.update_event.call_args.kwargs
        self.assertEqual(call_kwargs['event_id'], 'gcal-abc')
        self.assertEqual(call_kwargs['start_time'].time(), time(20, 0))
        # end = start + duration(60)
        self.assertEqual(call_kwargs['end_time'].time(), time(21, 0))

    def test_google_calendar_skipped_when_no_event_id(self):
        # google_calendar_event_id なし → update_event 呼ばれない
        self.client.login(username='Owner User', password='ownerpass')
        with mock.patch('event.views.crud.GoogleCalendarService') as MockService:
            response = self.client.post(self.url, {'start_time': '20:00'})
        self.assertEqual(response.status_code, 302)
        MockService.assert_not_called()

    def test_db_updated_even_when_gcal_update_fails(self):
        self.event.google_calendar_event_id = 'gcal-abc'
        self.event.save(update_fields=['google_calendar_event_id'])

        self.client.login(username='Owner User', password='ownerpass')
        with mock.patch('event.views.crud.GoogleCalendarService') as MockService:
            instance = MockService.return_value
            instance.update_event.side_effect = Exception('gcal error')
            response = self.client.post(self.url, {'start_time': '20:00'}, follow=False)

        self.assertEqual(response.status_code, 302)
        # DB は更新済み
        self.event.refresh_from_db()
        self.assertEqual(self.event.start_time, time(20, 0))
        # エラーメッセージが積まれている
        messages_list = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any('Googleカレンダー' in str(m) for m in messages_list),
            f"Expected Google カレンダー失敗メッセージ, got: {[str(m) for m in messages_list]}",
        )


class EventUpdateViewVketLockTest(EventUpdateViewBaseMixin, TestCase):
    def setUp(self):
        super().setUp()
        from vket.models import VketCollaboration, VketParticipation
        self.collaboration = VketCollaboration.objects.create(
            name='Vket Test',
            slug='vket-test',
            phase=VketCollaboration.Phase.LOCKED,
            period_start=self.event.date - timedelta(days=3),
            period_end=self.event.date + timedelta(days=3),
            registration_deadline=self.event.date - timedelta(days=10),
            lt_deadline=self.event.date - timedelta(days=5),
        )
        VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
        )

    def test_owner_blocked_during_vket_lock(self):
        self.client.login(username='Owner User', password='ownerpass')
        response = self.client.post(self.url, {'start_time': '20:00'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('event:my_list'))
        self.event.refresh_from_db()
        self.assertEqual(self.event.start_time, time(22, 0))

    def test_owner_get_blocked_during_vket_lock(self):
        self.client.login(username='Owner User', password='ownerpass')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('event:my_list'))

    def test_superuser_can_update_during_vket_lock(self):
        self.client.login(username='Super User', password='superpass')
        response = self.client.post(self.url, {'start_time': '20:00'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('event:my_list'))
        self.event.refresh_from_db()
        self.assertEqual(self.event.start_time, time(20, 0))
