"""EventUpdateView（開始時刻編集）のテスト。"""
from datetime import time, timedelta
from unittest import mock

from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from community.models import Community, CommunityMember
from event.models import Event, EventDetail
from event.sync_to_google import build_google_event_description
from twitter.models import TweetQueue
from user_account.models import CustomUser
from utils.vrchat_time import get_vrchat_today


class EventUpdateViewBaseMixin:
    """テスト共通のセットアップ。"""

    def setUp(self):
        self.client = Client()

        self.owner = CustomUser.objects.create_user(
            user_name='Owner User', email='owner@example.com',
        )
        self.staff = CustomUser.objects.create_user(
            user_name='Staff User', email='staff@example.com',
        )
        self.outsider = CustomUser.objects.create_user(
            user_name='Outsider User', email='out@example.com',
        )
        self.superuser = CustomUser.objects.create_superuser(
            user_name='Super User', email='super@example.com', password=None,
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
        self.client.force_login(self.owner)
        response = self.client.post(self.url, {'start_time': '21:00'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('event:my_list'))
        self.event.refresh_from_db()
        self.assertEqual(self.event.start_time, time(21, 0))

    def test_staff_can_update(self):
        self.client.force_login(self.staff)
        response = self.client.post(self.url, {'start_time': '21:30'})
        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.start_time, time(21, 30))

    def test_outsider_cannot_update(self):
        self.client.force_login(self.outsider)
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
        self.client.force_login(self.owner)
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
        self.client.force_login(self.owner)
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

        self.client.force_login(self.owner)
        with mock.patch('event.views.crud_event.GoogleCalendarService') as MockService:
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
        self.client.force_login(self.owner)
        with mock.patch('event.views.crud_event.GoogleCalendarService') as MockService:
            response = self.client.post(self.url, {'start_time': '20:00'})
        self.assertEqual(response.status_code, 302)
        MockService.assert_not_called()

    def test_db_updated_even_when_gcal_update_fails(self):
        self.event.google_calendar_event_id = 'gcal-abc'
        self.event.save(update_fields=['google_calendar_event_id'])

        self.client.force_login(self.owner)
        with mock.patch('event.views.crud_event.GoogleCalendarService') as MockService:
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
        self.client.force_login(self.owner)
        response = self.client.post(self.url, {'start_time': '20:00'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('event:my_list'))
        self.event.refresh_from_db()
        self.assertEqual(self.event.start_time, time(22, 0))

    def test_owner_get_blocked_during_vket_lock(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('event:my_list'))

    def test_superuser_can_update_during_vket_lock(self):
        self.client.force_login(self.superuser)
        response = self.client.post(self.url, {'start_time': '20:00'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('event:my_list'))
        self.event.refresh_from_db()
        self.assertEqual(self.event.start_time, time(20, 0))


class EventUpdateViewPastEventTest(EventUpdateViewBaseMixin, TestCase):
    """過去イベントは URL 直叩きでも編集不可（PR #544 Cursor 指摘）。"""

    def setUp(self):
        super().setUp()
        # yesterday（VRChat today より確実に過去）に付け替え
        yesterday = get_vrchat_today() - timedelta(days=1)
        # 一意制約回避のため時刻を変える
        self.event.date = yesterday
        self.event.start_time = time(20, 0)
        self.event.save(update_fields=['date', 'start_time'])

    def test_owner_post_to_past_event_blocked(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.url, {'start_time': '19:00'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('event:my_list'))
        self.event.refresh_from_db()
        # DB 不変
        self.assertEqual(self.event.start_time, time(20, 0))

    def test_owner_get_to_past_event_blocked(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('event:my_list'))


class EventUpdateViewIndexCacheTest(EventUpdateViewBaseMixin, TestCase):
    """clear_index_view_cache が実キャッシュキー（get_vrchat_today ベース）で呼ばれる。"""

    def test_clear_index_view_cache_called_without_args(self):
        self.client.force_login(self.owner)
        with mock.patch('event.views.crud_event.clear_index_view_cache') as mock_clear:
            response = self.client.post(self.url, {'start_time': '21:00'})
        self.assertEqual(response.status_code, 302)
        mock_clear.assert_called_once_with()


class EventUpdateViewGoogleCalendarDescriptionTest(EventUpdateViewBaseMixin, TestCase):
    """GCal patch に description（sync 側と同じ生成関数の出力）が渡る。"""

    def test_update_event_receives_description(self):
        self.event.google_calendar_event_id = 'gcal-abc'
        self.event.save(update_fields=['google_calendar_event_id'])

        self.client.force_login(self.owner)
        with mock.patch('event.views.crud_event.GoogleCalendarService') as MockService:
            instance = MockService.return_value
            response = self.client.post(self.url, {'start_time': '20:00'})

        self.assertEqual(response.status_code, 302)
        instance.update_event.assert_called_once()
        call_kwargs = instance.update_event.call_args.kwargs

        # description は sync 側の生成関数と同一出力
        self.event.refresh_from_db()
        expected = build_google_event_description(self.event)
        self.assertEqual(call_kwargs['description'], expected)
        # 新時刻が description 本文に反映されている
        self.assertIn('20:00', call_kwargs['description'])
        # summary は渡さない（community 名で不変のため）
        self.assertNotIn('summary', call_kwargs)


class EventUpdateViewTweetQueueResetTest(EventUpdateViewBaseMixin, TestCase):
    """開始時刻変更で未投稿 TweetQueue を再生成対象に戻す（PR #544 Cursor 指摘）。"""

    def _make_queue(self, status: str) -> TweetQueue:
        return TweetQueue.objects.create(
            tweet_type='daily_reminder',
            community=self.community,
            event=self.event,
            generated_text='OLD TEXT with 22:00',
            status=status,
            generation_token='oldtoken123',
            scheduled_at=timezone.now() + timedelta(days=1),
        )

    def test_unposted_queue_reset_to_regeneration(self):
        ready = self._make_queue('ready')

        self.client.force_login(self.owner)
        response = self.client.post(self.url, {'start_time': '21:00'})
        self.assertEqual(response.status_code, 302)

        ready.refresh_from_db()
        self.assertEqual(ready.status, 'generation_failed')
        self.assertEqual(ready.generated_text, '')
        # in-flight 生成の write-back を無効化するためトークンも空になる
        self.assertEqual(ready.generation_token, '')

    def test_posted_queue_not_touched(self):
        posted = TweetQueue.objects.create(
            tweet_type='lt',
            community=self.community,
            event=self.event,
            generated_text='POSTED TEXT',
            status='posted',
            scheduled_at=timezone.now() - timedelta(hours=1),
            posted_at=timezone.now() - timedelta(hours=1),
        )
        failed = TweetQueue.objects.create(
            tweet_type='lt',
            community=self.community,
            event=self.event,
            generated_text='FAILED TEXT',
            status='failed',
            scheduled_at=timezone.now() - timedelta(hours=1),
        )

        self.client.force_login(self.owner)
        response = self.client.post(self.url, {'start_time': '21:00'})
        self.assertEqual(response.status_code, 302)

        posted.refresh_from_db()
        failed.refresh_from_db()
        # posted / failed は不変
        self.assertEqual(posted.status, 'posted')
        self.assertEqual(posted.generated_text, 'POSTED TEXT')
        self.assertEqual(failed.status, 'failed')
        self.assertEqual(failed.generated_text, 'FAILED TEXT')


class EventUpdateViewNoOpSaveTest(EventUpdateViewBaseMixin, TestCase):
    """時刻を変えない保存では副作用を走らせない（PR #544 Cursor 指摘）。"""

    def test_noop_save_keeps_queue_and_skips_gcal(self):
        queue = TweetQueue.objects.create(
            tweet_type='daily_reminder',
            community=self.community,
            event=self.event,
            generated_text='READY TEXT with 22:00',
            status='ready',
            scheduled_at=timezone.now() + timedelta(days=1),
        )
        self.event.google_calendar_event_id = 'gcal-abc'
        self.event.save(update_fields=['google_calendar_event_id'])

        self.client.force_login(self.owner)
        with mock.patch('event.views.crud_event.GoogleCalendarService') as service_cls:
            # 現在値と同じ 22:00 のまま保存
            response = self.client.post(self.url, {'start_time': '22:00'})

        self.assertEqual(response.status_code, 302)
        queue.refresh_from_db()
        self.assertEqual(queue.status, 'ready')
        self.assertEqual(queue.generated_text, 'READY TEXT with 22:00')
        service_cls.assert_not_called()

        # 変更が無いのに「変更しました」と出さない
        from django.contrib.messages import get_messages
        message_texts = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertIn('開始時刻に変更はありません。', message_texts)
        self.assertNotIn('開始時刻を変更しました。', message_texts)
