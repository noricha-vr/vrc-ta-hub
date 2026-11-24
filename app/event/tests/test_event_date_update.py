"""Event開催日変更の振る舞いを検証する。"""

from datetime import datetime, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format, time_format

from community.constants import weekday_code
from community.models import Community, CommunityMember
from event.models import Event, EventOccurrenceTombstone, RecurrenceRule
from event_calendar.calendar_utils import generate_google_calendar_url
from ta_hub.index_cache import get_index_view_cache_key
from twitter.models import TweetQueue
from twitter.scheduling import scheduled_at_for_date
from utils.vrchat_time import get_vrchat_today
from vket.models import VketCollaboration, VketParticipation


User = get_user_model()


class EventDateUpdateViewTests(TestCase):
    def setUp(self):
        cache.clear()
        generate_google_calendar_url.cache_clear()
        self.client = Client()
        self.owner = User.objects.create_user(
            user_name='date_owner',
            email='date-owner@example.com',
            password='testpass123',
        )
        self.other_user = User.objects.create_user(
            user_name='date_other',
            email='date-other@example.com',
            password='testpass123',
        )
        self.superuser = User.objects.create_superuser(
            user_name='date_admin',
            email='date-admin@example.com',
            password='testpass123',
        )
        self.community = Community.objects.create(
            name='日付変更集会',
            status='approved',
            start_time=time(22, 0),
            duration=60,
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner,
            role=CommunityMember.Role.OWNER,
        )
        # 実装の「今日」は VRChat 時間（04:00 JST 境界）。localdate()（00:00 境界）を
        # 使うと 00:00-04:00 JST の実行時だけ1日ズレてフレークする（Issue #555）
        self.today = get_vrchat_today()
        self.original_date = self.today + timedelta(days=7)
        self.event = Event.objects.create(
            community=self.community,
            date=self.original_date,
            start_time=time(22, 0),
            duration=60,
            weekday=self.original_date.strftime('%a').upper()[:3],
        )
        self.url = reverse(
            'event:date_update',
            kwargs={'pk': self.event.pk},
        )

    def tearDown(self):
        generate_google_calendar_url.cache_clear()
        cache.clear()

    def _post_date(self, new_date):
        return self.client.post(
            self.url,
            {'date': new_date.isoformat()},
        )

    def test_owner_moves_child_and_records_rescheduled_tombstone(self):
        """開始時刻を保持し、子開催回を系列から外して元日を記録する"""
        rule = RecurrenceRule.objects.create(
            community=self.community,
            frequency='WEEKLY',
        )
        master = Event.objects.create(
            community=self.community,
            date=self.today,
            start_time=time(22, 0),
            duration=60,
            is_recurring_master=True,
            recurrence_rule=rule,
        )
        self.event.recurring_master = master
        self.event.save(update_fields=['recurring_master'])
        new_date = self.original_date + timedelta(days=2)
        self.client.force_login(self.owner)

        response = self._post_date(new_date)

        self.assertRedirects(response, reverse('event:my_list'))
        self.event.refresh_from_db()
        self.assertEqual(self.event.date, new_date)
        self.assertEqual(self.event.start_time, time(22, 0))
        self.assertEqual(
            self.event.weekday,
            weekday_code(new_date),
        )
        self.assertIsNone(self.event.recurring_master_id)
        tombstone = EventOccurrenceTombstone.objects.get(
            community=self.community,
            date=self.original_date,
        )
        self.assertEqual(
            tombstone.reason,
            EventOccurrenceTombstone.Reason.RESCHEDULED,
        )
        master.refresh_from_db()
        self.assertTrue(master.is_recurring_master)
        self.assertEqual(master.recurrence_rule, rule)

    def test_moving_master_keeps_recurrence_anchor(self):
        """親イベントの日付を変更しても系列アンカー属性は維持する"""
        rule = RecurrenceRule.objects.create(
            community=self.community,
            frequency='WEEKLY',
        )
        self.event.is_recurring_master = True
        self.event.recurrence_rule = rule
        self.event.save(update_fields=['is_recurring_master', 'recurrence_rule'])
        self.client.force_login(self.owner)

        self._post_date(self.original_date + timedelta(days=1))

        self.event.refresh_from_db()
        self.assertTrue(self.event.is_recurring_master)
        self.assertEqual(self.event.recurrence_rule, rule)

    @patch('event.views.crud_event.GoogleCalendarService')
    def test_duplicate_date_is_rejected_before_google_update(
        self,
        calendar_service_class,
    ):
        duplicate_date = self.original_date + timedelta(days=1)
        Event.objects.create(
            community=self.community,
            date=duplicate_date,
            start_time=self.event.start_time,
            duration=60,
        )
        self.event.google_calendar_event_id = 'calendar-id'
        self.event.save(update_fields=['google_calendar_event_id'])
        self.client.force_login(self.owner)

        response = self._post_date(duplicate_date)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '既に存在します')
        self.event.refresh_from_db()
        self.assertEqual(self.event.date, self.original_date)
        self.assertFalse(EventOccurrenceTombstone.objects.exists())
        calendar_service_class.return_value.update_event.assert_not_called()

    def test_past_date_is_rejected(self):
        self.client.force_login(self.owner)

        response = self._post_date(self.today - timedelta(days=1))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '本日以降')
        self.event.refresh_from_db()
        self.assertEqual(self.event.date, self.original_date)

    def test_min_date_uses_vrchat_boundary_in_late_night_window(self):
        """00:00-04:00 JST では前日が「今日」として提示される（Issue #555 の再発防止）。"""
        self.client.force_login(self.owner)
        frozen_now = timezone.make_aware(datetime(2026, 7, 29, 1, 0))

        with patch('utils.vrchat_time.timezone.now', return_value=frozen_now):
            response = self.client.get(self.url)

        self.assertContains(response, 'min="2026-07-28"')

    def test_non_member_cannot_update(self):
        self.client.force_login(self.other_user)

        response = self._post_date(self.original_date + timedelta(days=1))

        self.assertRedirects(response, reverse('event:my_list'))
        self.event.refresh_from_db()
        self.assertEqual(self.event.date, self.original_date)

    @patch('event.views.crud_event.GoogleCalendarService')
    def test_google_update_runs_after_database_move(
        self,
        calendar_service_class,
    ):
        self.event.google_calendar_event_id = 'calendar-id'
        self.event.save(update_fields=['google_calendar_event_id'])
        new_date = self.original_date + timedelta(days=1)
        self.client.force_login(self.owner)

        def assert_database_is_authoritative(**_kwargs):
            moved_event = Event.objects.get(pk=self.event.pk)
            self.assertEqual(moved_event.date, new_date)
            self.assertTrue(
                EventOccurrenceTombstone.objects.filter(
                    community=self.community,
                    date=self.original_date,
                ).exists()
            )

        calendar_service_class.return_value.update_event.side_effect = (
            assert_database_is_authoritative
        )
        response = self._post_date(new_date)

        self.assertRedirects(response, reverse('event:my_list'))
        calendar_service_class.return_value.update_event.assert_called_once()
        call_kwargs = (
            calendar_service_class.return_value.update_event.call_args.kwargs
        )
        self.assertEqual(call_kwargs['event_id'], 'calendar-id')
        self.assertEqual(call_kwargs['start_time'].date(), new_date)
        self.event.refresh_from_db()
        self.assertEqual(self.event.date, new_date)

    @patch('event.views.crud_event.GoogleCalendarService')
    def test_google_update_includes_new_date_in_description(
        self,
        calendar_service_class,
    ):
        """description も新日付で再生成する（旧日付が残ると同期が skipped で矛盾固定される）"""
        self.event.google_calendar_event_id = 'calendar-id'
        self.event.save(update_fields=['google_calendar_event_id'])
        new_date = self.original_date + timedelta(days=1)
        self.client.force_login(self.owner)

        self._post_date(new_date)

        call_kwargs = (
            calendar_service_class.return_value.update_event.call_args.kwargs
        )
        self.assertIn('description', call_kwargs)
        self.assertIn(
            f"{new_date.year}年{new_date.month}月{new_date.day}日",
            call_kwargs['description'],
        )
        self.assertNotIn(
            f"{self.original_date.year}年{self.original_date.month}月{self.original_date.day}日",
            call_kwargs['description'],
        )

    def test_past_event_cannot_be_opened(self):
        """過去イベントの開催日変更は URL 直叩きでもブロックする（鉛筆と同基準）"""
        past_event = Event.objects.create(
            community=self.community,
            date=self.today - timedelta(days=3),
            start_time=time(22, 0),
            duration=60,
            weekday='MON',
        )
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse('event:date_update', kwargs={'pk': past_event.pk}),
        )

        self.assertRedirects(response, reverse('event:my_list'))

    @patch('event.views.crud_event.GoogleCalendarService')
    def test_google_update_failure_keeps_database_move(
        self,
        calendar_service_class,
    ):
        self.event.google_calendar_event_id = 'calendar-id'
        self.event.save(update_fields=['google_calendar_event_id'])
        calendar_service_class.return_value.update_event.side_effect = RuntimeError(
            'calendar unavailable'
        )
        self.client.force_login(self.owner)
        new_date = self.original_date + timedelta(days=1)

        response = self.client.post(
            self.url,
            {'date': new_date.isoformat()},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '開催日は変更しました')
        self.assertContains(response, '後続の同期で再反映します')
        self.event.refresh_from_db()
        self.assertEqual(self.event.date, new_date)
        self.assertTrue(
            EventOccurrenceTombstone.objects.filter(
                community=self.community,
                date=self.original_date,
                reason=EventOccurrenceTombstone.Reason.RESCHEDULED,
            ).exists()
        )

    def test_form_shows_current_schedule_and_minimum_date(self):
        self.client.force_login(self.owner)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '現在の開催日時:')
        self.assertContains(
            response,
            date_format(self.original_date),
        )
        self.assertContains(
            response,
            time_format(self.event.start_time),
        )
        self.assertContains(
            response,
            f'min="{self.today.isoformat()}"',
        )
        self.assertContains(response, '開催日は本日以降のみ指定できます')

    def test_child_form_explains_only_this_occurrence_changes(self):
        master = self._make_recurring_master()
        self.event.recurring_master = master
        self.event.save(update_fields=['recurring_master'])
        self.client.force_login(self.owner)

        response = self.client.get(self.url)

        self.assertContains(response, 'この開催回だけを変更します')
        self.assertContains(response, '以後の定期開催スケジュールは変更されません')

    def test_master_form_explains_children_and_rule_stay_unchanged(self):
        rule = RecurrenceRule.objects.create(
            community=self.community,
            frequency='WEEKLY',
        )
        self.event.is_recurring_master = True
        self.event.recurrence_rule = rule
        self.event.save(update_fields=['is_recurring_master', 'recurrence_rule'])
        self.client.force_login(self.owner)

        response = self.client.get(self.url)

        self.assertContains(response, 'この親イベントの開催日だけを変更します')
        self.assertContains(response, '登録済みの子開催回と定期ルールは変更されません')

    def test_unposted_daily_reminder_is_skipped_on_date_move(self):
        original_schedule = scheduled_at_for_date(self.original_date)
        queue = TweetQueue.objects.create(
            tweet_type='daily_reminder',
            community=self.community,
            event=self.event,
            status='ready',
            scheduled_at=original_schedule,
        )
        new_date = self.original_date + timedelta(days=1)
        self.client.force_login(self.owner)

        self._post_date(new_date)

        queue.refresh_from_db()
        self.assertEqual(queue.scheduled_at, original_schedule)
        self.assertEqual(queue.status, 'skipped')
        self.assertIn('廃止', queue.error_message)

    def test_posted_daily_reminder_is_unchanged(self):
        original_schedule = scheduled_at_for_date(self.original_date)
        queue = TweetQueue.objects.create(
            tweet_type='daily_reminder',
            community=self.community,
            event=self.event,
            status='posted',
            scheduled_at=original_schedule,
            posted_at=timezone.now(),
        )
        self.client.force_login(self.owner)

        self._post_date(self.original_date + timedelta(days=1))

        queue.refresh_from_db()
        self.assertEqual(queue.scheduled_at, original_schedule)
        self.assertEqual(queue.status, 'posted')

    def test_past_rescheduled_reminder_is_skipped(self):
        """日付移動時、残存していた未投稿の daily_reminder も skipped にする"""
        original_schedule = scheduled_at_for_date(self.event.date)
        queue = TweetQueue.objects.create(
            tweet_type='daily_reminder',
            community=self.community,
            event=self.event,
            status='ready',
            scheduled_at=original_schedule,
        )
        self.client.force_login(self.owner)

        self._post_date(self.today)

        queue.refresh_from_db()
        self.assertEqual(queue.scheduled_at, original_schedule)
        self.assertEqual(queue.status, 'skipped')

    def test_moving_event_forward_reschedules_and_resets_unposted_lt_queue(self):
        """開催日を当日へ前倒しした時、未投稿の lt キューが新日程向けに再予約・再生成待ちになる"""
        self.event.start_time = time(22, 0)
        self.event.save(update_fields=['start_time'])
        old_schedule = timezone.make_aware(
            datetime.combine(self.today + timedelta(days=1), time(12, 0)),
            timezone.get_current_timezone(),
        )
        lt_queue = TweetQueue.objects.create(
            tweet_type='lt',
            community=self.community,
            event=self.event,
            status='ready',
            generated_text='旧開催日の告知',
            generation_token='old-token',
            scheduled_at=old_schedule,
        )
        posted_queue = TweetQueue.objects.create(
            tweet_type='special',
            community=self.community,
            event=self.event,
            status='posted',
            generated_text='投稿済み告知',
            scheduled_at=old_schedule,
            posted_at=timezone.now(),
        )
        self.client.force_login(self.owner)
        move_now = timezone.make_aware(
            datetime.combine(self.today, time(15, 0)),
            timezone.get_current_timezone(),
        )

        with (
            patch('event.services.recurrence_override.timezone.now', return_value=move_now),
            patch('twitter.scheduling.timezone.now', return_value=move_now),
        ):
            self._post_date(self.today)

        lt_queue.refresh_from_db()
        posted_queue.refresh_from_db()
        self.assertEqual(lt_queue.scheduled_at, move_now)
        self.assertEqual(lt_queue.status, 'generation_failed')
        self.assertEqual(lt_queue.generated_text, '')
        self.assertEqual(lt_queue.generation_token, '')
        self.assertEqual(posted_queue.status, 'posted')
        self.assertEqual(posted_queue.generated_text, '投稿済み告知')

    def test_moving_started_event_back_to_future_revives_past_start_skipped_queue_only_for_eligible_details(self):
        """開始済み日程でスキップされた lt キューを未来日へ戻すと、有効な発表のみ再予約される"""
        from event.models import EventDetail
        from event.services.recurrence_override import MOVED_PAST_START_SKIP_REASON
        from tests.factories import make_event_detail

        self.event.start_time = time(22, 0)
        self.event.save(update_fields=['start_time'])
        active_detail = make_event_detail(
            self.event,
            detail_type='LT',
            status='approved',
            speaker='有効太郎',
            theme='有効な発表',
            start_time=time(22, 10),
        )
        deleted_detail = make_event_detail(
            self.event,
            detail_type='LT',
            status='approved',
            speaker='削除花子',
            theme='削除される発表',
            start_time=time(22, 30),
        )
        old_schedule = timezone.make_aware(
            datetime.combine(self.today + timedelta(days=1), time(9, 0)),
            timezone.get_current_timezone(),
        )
        active_queue = TweetQueue.objects.create(
            tweet_type='lt',
            community=self.community,
            event=self.event,
            event_detail=active_detail,
            status='ready',
            generated_text='元の告知文',
            scheduled_at=old_schedule,
        )
        deleted_queue = TweetQueue.objects.create(
            tweet_type='lt',
            community=self.community,
            event=self.event,
            event_detail=deleted_detail,
            status='ready',
            generated_text='削除対象の告知文',
            scheduled_at=old_schedule,
        )
        self.client.force_login(self.owner)
        past_move_now = timezone.make_aware(
            datetime.combine(self.today, time(23, 0)),
            timezone.get_current_timezone(),
        )

        with (
            patch('event.services.recurrence_override.timezone.now', return_value=past_move_now),
            patch('twitter.scheduling.timezone.now', return_value=past_move_now),
        ):
            self._post_date(self.today)

        active_queue.refresh_from_db()
        deleted_queue.refresh_from_db()
        self.assertEqual(active_queue.status, 'skipped')
        self.assertEqual(active_queue.error_message, MOVED_PAST_START_SKIP_REASON)
        self.assertEqual(deleted_queue.status, 'skipped')

        EventDetail.objects.filter(pk=deleted_detail.pk).delete()
        future_date = self.today + timedelta(days=3)

        with (
            patch('event.services.recurrence_override.timezone.now', return_value=past_move_now),
            patch('twitter.scheduling.timezone.now', return_value=past_move_now),
        ):
            self._post_date(future_date)

        active_queue.refresh_from_db()
        deleted_queue.refresh_from_db()
        self.assertEqual(active_queue.status, 'generation_failed')
        self.assertEqual(active_queue.error_message, '開催日変更により再生成')
        self.assertEqual(active_queue.generated_text, '')
        self.assertNotEqual(active_queue.scheduled_at, old_schedule)
        self.assertEqual(deleted_queue.status, 'skipped')

    def test_moving_started_event_to_future_revives_dispatch_event_started_skip(self):
        """通常配信でイベント開始済みスキップになった lt キューも未来日移動で再予約される"""
        from twitter.services.announcement_eligibility import EVENT_STARTED_SKIP_MESSAGE

        self.event.start_time = time(22, 0)
        self.event.save(update_fields=['start_time'])
        old_schedule = timezone.make_aware(
            datetime.combine(self.today, time(20, 0)),
            timezone.get_current_timezone(),
        )
        dispatched_skipped = TweetQueue.objects.create(
            tweet_type='lt',
            community=self.community,
            event=self.event,
            status='skipped',
            error_message=EVENT_STARTED_SKIP_MESSAGE,
            scheduled_at=old_schedule,
        )
        future_date = self.today + timedelta(days=3)
        move_now = timezone.make_aware(
            datetime.combine(self.today, time(23, 0)),
            timezone.get_current_timezone(),
        )
        self.client.force_login(self.owner)

        with (
            patch('event.services.recurrence_override.timezone.now', return_value=move_now),
            patch('twitter.scheduling.timezone.now', return_value=move_now),
        ):
            self._post_date(future_date)

        dispatched_skipped.refresh_from_db()
        self.assertEqual(dispatched_skipped.status, 'generation_failed')
        self.assertEqual(dispatched_skipped.error_message, '開催日変更により再生成')
        self.assertNotEqual(dispatched_skipped.scheduled_at, old_schedule)

    def test_moving_started_event_to_future_revives_skipped_misskey_note_without_touching_posted_or_unknown(self):
        """未来日移動で開始済みスキップの TweetQueue と MisskeyNote(skipped) は復帰し、posted/unknown は不変"""
        from twitter.models import MisskeyNote
        from twitter.services.announcement_eligibility import EVENT_STARTED_SKIP_MESSAGE

        self.event.start_time = time(22, 0)
        self.event.save(update_fields=['start_time'])
        old_schedule = timezone.make_aware(
            datetime.combine(self.today, time(20, 0)),
            timezone.get_current_timezone(),
        )
        skipped_queue = TweetQueue.objects.create(
            tweet_type='lt',
            community=self.community,
            event=self.event,
            status='skipped',
            error_message=EVENT_STARTED_SKIP_MESSAGE,
            scheduled_at=old_schedule,
        )
        skipped_note = MisskeyNote.objects.create(
            tweet_queue=skipped_queue,
            status='skipped',
            error_message='告知対象外のため投稿をスキップ (event_started)',
            attempts=2,
        )
        posted_queue = TweetQueue.objects.create(
            tweet_type='special',
            community=self.community,
            event=self.event,
            status='ready',
            generated_text='既存の準備済み投稿',
            scheduled_at=old_schedule,
        )
        posted_note = MisskeyNote.objects.create(
            tweet_queue=posted_queue,
            status='posted',
            note_id='note-posted-1',
            posted_at=timezone.now(),
        )
        unknown_queue = TweetQueue.objects.create(
            tweet_type='lt',
            community=self.community,
            event=self.event,
            status='skipped',
            error_message=EVENT_STARTED_SKIP_MESSAGE,
            scheduled_at=old_schedule,
        )
        unknown_note = MisskeyNote.objects.create(
            tweet_queue=unknown_queue,
            status='unknown',
            error_message='タイムアウト',
            attempts=1,
        )
        future_date = self.today + timedelta(days=3)
        move_now = timezone.make_aware(
            datetime.combine(self.today, time(23, 0)),
            timezone.get_current_timezone(),
        )
        self.client.force_login(self.owner)

        with (
            patch('event.services.recurrence_override.timezone.now', return_value=move_now),
            patch('twitter.scheduling.timezone.now', return_value=move_now),
        ):
            self._post_date(future_date)

        skipped_queue.refresh_from_db()
        skipped_note.refresh_from_db()
        posted_note.refresh_from_db()
        unknown_note.refresh_from_db()
        self.assertEqual(skipped_queue.status, 'generation_failed')
        self.assertEqual(skipped_note.status, 'pending')
        self.assertEqual(skipped_note.error_message, '')
        self.assertEqual(skipped_note.attempts, 0)
        self.assertEqual(posted_note.status, 'posted')
        self.assertEqual(posted_note.note_id, 'note-posted-1')
        self.assertEqual(unknown_note.status, 'unknown')
        self.assertEqual(unknown_note.attempts, 1)

    def test_moving_event_to_future_revives_date_passed_and_expired_queues_but_keeps_api_failures(self):
        """過去日失敗・期限切れスキップの発表告知は未来日移動で復帰し、API投稿失敗キューは触らない"""
        from twitter.services.announcement_eligibility import (
            EVENT_DATE_PASSED_SKIP_MESSAGE,
            SCHEDULE_EXPIRED_SKIP_REASON,
        )

        self.event.start_time = time(22, 0)
        self.event.save(update_fields=['start_time'])
        old_schedule = timezone.make_aware(
            datetime.combine(self.today - timedelta(days=2), time(12, 0)),
            timezone.get_current_timezone(),
        )
        passed_queue = TweetQueue.objects.create(
            tweet_type='lt',
            community=self.community,
            event=self.event,
            status='failed',
            error_message=EVENT_DATE_PASSED_SKIP_MESSAGE,
            scheduled_at=old_schedule,
        )
        expired_queue = TweetQueue.objects.create(
            tweet_type='special',
            community=self.community,
            event=self.event,
            status='skipped',
            error_message=SCHEDULE_EXPIRED_SKIP_REASON,
            scheduled_at=old_schedule,
        )
        api_failed_queue = TweetQueue.objects.create(
            tweet_type='lt',
            community=self.community,
            event=self.event,
            status='failed',
            error_message='X API投稿に失敗 (status=500)',
            scheduled_at=old_schedule,
        )
        future_date = self.today + timedelta(days=3)
        move_now = timezone.make_aware(
            datetime.combine(self.today, time(23, 0)),
            timezone.get_current_timezone(),
        )
        self.client.force_login(self.owner)

        with (
            patch('event.services.recurrence_override.timezone.now', return_value=move_now),
            patch('twitter.scheduling.timezone.now', return_value=move_now),
        ):
            self._post_date(future_date)

        passed_queue.refresh_from_db()
        expired_queue.refresh_from_db()
        api_failed_queue.refresh_from_db()
        self.assertEqual(passed_queue.status, 'generation_failed')
        self.assertEqual(passed_queue.error_message, '開催日変更により再生成')
        self.assertNotEqual(passed_queue.scheduled_at, old_schedule)
        self.assertEqual(expired_queue.status, 'generation_failed')
        self.assertEqual(expired_queue.error_message, '開催日変更により再生成')
        self.assertNotEqual(expired_queue.scheduled_at, old_schedule)
        self.assertEqual(api_failed_queue.status, 'failed')
        self.assertEqual(api_failed_queue.error_message, 'X API投稿に失敗 (status=500)')
        self.assertEqual(api_failed_queue.scheduled_at, old_schedule)

    def test_moving_event_to_future_does_not_revive_duplicate_legacy_or_posted_detail_queues(self):
        """日時変更で同一発表に有効行や投稿済み行がある場合は重複して旧スキップ行を復帰させない"""
        from tests.factories import make_event_detail
        from twitter.services.announcement_eligibility import (
            EVENT_STARTED_SKIP_MESSAGE,
            LEGACY_SAME_DAY_INDIVIDUAL_SKIP_REASON,
        )

        self.event.start_time = time(22, 0)
        self.event.save(update_fields=['start_time'])
        old_schedule = timezone.make_aware(
            datetime.combine(self.today, time(20, 0)),
            timezone.get_current_timezone(),
        )
        detail_ready = make_event_detail(
            self.event, detail_type='LT', status='approved', speaker='重複A', theme='有効あり', start_time=time(22, 10),
        )
        detail_posted = make_event_detail(
            self.event, detail_type='LT', status='approved', speaker='重複B', theme='投稿済みあり', start_time=time(22, 20),
        )
        detail_double_skip = make_event_detail(
            self.event, detail_type='LT', status='approved', speaker='重複C', theme='スキップ2件', start_time=time(22, 30),
        )
        TweetQueue.objects.filter(
            event_detail__in=(detail_ready, detail_posted, detail_double_skip),
        ).delete()

        q_ready = TweetQueue.objects.create(
            tweet_type='lt', community=self.community, event=self.event, event_detail=detail_ready,
            status='ready', generated_text='有効', scheduled_at=old_schedule,
        )
        q_ready_legacy_dup = TweetQueue.objects.create(
            tweet_type='lt', community=self.community, event=self.event, event_detail=detail_ready,
            status='skipped', error_message=LEGACY_SAME_DAY_INDIVIDUAL_SKIP_REASON, scheduled_at=old_schedule,
        )
        q_posted = TweetQueue.objects.create(
            tweet_type='lt', community=self.community, event=self.event, event_detail=detail_posted,
            status='posted', generated_text='投稿済み', scheduled_at=old_schedule, posted_at=timezone.now(),
        )
        q_posted_legacy_dup = TweetQueue.objects.create(
            tweet_type='lt', community=self.community, event=self.event, event_detail=detail_posted,
            status='skipped', error_message=LEGACY_SAME_DAY_INDIVIDUAL_SKIP_REASON, scheduled_at=old_schedule,
        )
        q_skip_old = TweetQueue.objects.create(
            tweet_type='lt', community=self.community, event=self.event, event_detail=detail_double_skip,
            status='skipped', error_message=EVENT_STARTED_SKIP_MESSAGE, scheduled_at=old_schedule,
        )
        q_skip_new = TweetQueue.objects.create(
            tweet_type='lt', community=self.community, event=self.event, event_detail=detail_double_skip,
            status='skipped', error_message=EVENT_STARTED_SKIP_MESSAGE, scheduled_at=old_schedule,
        )

        future_date = self.today + timedelta(days=3)
        move_now = timezone.make_aware(
            datetime.combine(self.today, time(23, 0)),
            timezone.get_current_timezone(),
        )
        self.client.force_login(self.owner)
        with (
            patch('event.services.recurrence_override.timezone.now', return_value=move_now),
            patch('twitter.scheduling.timezone.now', return_value=move_now),
        ):
            self._post_date(future_date)

        for q in (q_ready, q_ready_legacy_dup, q_posted, q_posted_legacy_dup, q_skip_old, q_skip_new):
            q.refresh_from_db()
        self.assertEqual(q_ready.status, 'generation_failed')
        self.assertEqual(q_ready_legacy_dup.status, 'skipped')
        self.assertEqual(q_posted.status, 'posted')
        self.assertEqual(q_posted_legacy_dup.status, 'skipped')
        self.assertEqual(q_skip_new.status, 'generation_failed')
        self.assertEqual(q_skip_old.status, 'skipped')

    def test_bulk_delete_then_date_move_then_bulk_restore_reuses_and_revives_queue(self):
        """一括論理削除 -> 未来日への移動 -> 一括復元の順でもキューが再利用され告知が復帰する"""
        from event.models import EventDetail
        from tests.factories import make_event_detail
        from twitter.services.announcement_eligibility import RETRACTED_PRESENTATION_SKIP_MESSAGE

        self.event.start_time = time(22, 0)
        self.event.save(update_fields=['start_time'])
        detail = make_event_detail(
            self.event, detail_type='LT', status='approved', speaker='復元連携太郎', theme='削除移動復元テスト', start_time=time(22, 10),
        )
        queue = TweetQueue.objects.get(event_detail=detail, tweet_type='lt')
        queue.status = 'ready'
        queue.generated_text = '元の文面'
        queue.save(update_fields=['status', 'generated_text'])

        EventDetail.objects.filter(pk=detail.pk).delete()
        future_date = self.today + timedelta(days=3)
        move_now = timezone.make_aware(
            datetime.combine(self.today, time(23, 0)),
            timezone.get_current_timezone(),
        )
        self.client.force_login(self.owner)
        with (
            patch('event.services.recurrence_override.timezone.now', return_value=move_now),
            patch('twitter.scheduling.timezone.now', return_value=move_now),
        ):
            self._post_date(future_date)

        queue.refresh_from_db()
        self.assertEqual(queue.status, 'skipped')
        self.assertEqual(queue.error_message, RETRACTED_PRESENTATION_SKIP_MESSAGE)

        with (
            patch('twitter.signals.timezone.now', return_value=move_now),
            patch('twitter.scheduling.timezone.now', return_value=move_now),
            patch('event.models.timezone.now', return_value=move_now),
        ):
            EventDetail.all_objects.filter(pk=detail.pk).restore()

        queue.refresh_from_db()
        self.assertEqual(TweetQueue.objects.filter(event_detail=detail, tweet_type='lt').count(), 1)
        self.assertEqual(queue.status, 'generating')
        self.assertEqual(queue.generated_text, '')

    def test_commit_invalidates_index_and_calendar_url_caches(self):
        request = RequestFactory().get('/')
        generate_google_calendar_url(request, self.event)
        self.assertGreater(
            generate_google_calendar_url.cache_info().currsize,
            0,
        )
        cache_keys = [
            get_index_view_cache_key(),
            f'google_calendar_url_{self.event.pk}',
            f'calendar_entry_url_{self.event.pk}',
            f'calendar_entry_url_{self.event.pk}_False',
            f'calendar_entry_url_{self.event.pk}_True',
        ]
        for key in cache_keys:
            cache.set(key, 'stale', 60)
        self.client.force_login(self.owner)

        with self.captureOnCommitCallbacks(execute=True):
            self._post_date(self.original_date + timedelta(days=1))

        for key in cache_keys:
            self.assertIsNone(cache.get(key))
        self.assertEqual(
            generate_google_calendar_url.cache_info().currsize,
            0,
        )

    def test_vket_lock_checks_old_date(self):
        self._create_vket_period(
            self.original_date,
            self.original_date + timedelta(days=1),
        )
        self.client.force_login(self.owner)

        response = self._post_date(self.original_date + timedelta(days=3))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '運営のみ')
        self.event.refresh_from_db()
        self.assertEqual(self.event.date, self.original_date)

    def test_vket_lock_checks_new_date(self):
        new_date = self.original_date + timedelta(days=3)
        self._create_vket_period(new_date, new_date + timedelta(days=1))
        self.client.force_login(self.owner)

        response = self._post_date(new_date)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '運営のみ')
        self.event.refresh_from_db()
        self.assertEqual(self.event.date, self.original_date)

    def test_vket_lock_checks_old_date_with_confirmed_match(self):
        """published_event 未設定でも、変更前日付が期間内ならブロックする"""
        self._create_vket_period(
            self.original_date,
            self.original_date + timedelta(days=1),
            use_confirmed_match=True,
        )
        self.client.force_login(self.owner)

        response = self._post_date(self.original_date + timedelta(days=3))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '運営のみ')
        self.event.refresh_from_db()
        self.assertEqual(self.event.date, self.original_date)

    def test_vket_lock_checks_new_date_with_confirmed_match(self):
        """published_event 未設定でも、移動先日付が期間内ならブロックする"""
        new_date = self.original_date + timedelta(days=3)
        self._create_vket_period(
            new_date,
            new_date + timedelta(days=1),
            use_confirmed_match=True,
        )
        self.client.force_login(self.owner)

        response = self._post_date(new_date)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '運営のみ')
        self.event.refresh_from_db()
        self.assertEqual(self.event.date, self.original_date)

    def test_vket_lock_blocks_move_onto_confirmed_date(self):
        """期間外のイベントを confirmed_date（期間内）へ動かす操作もブロックする"""
        confirmed_date = self.original_date + timedelta(days=3)
        self._create_vket_period(
            confirmed_date,
            confirmed_date + timedelta(days=1),
            use_confirmed_match=True,
            confirmed_date=confirmed_date,
        )
        self.client.force_login(self.owner)

        response = self._post_date(confirmed_date)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '運営のみ')
        self.event.refresh_from_db()
        self.assertEqual(self.event.date, self.original_date)

    def test_superuser_can_bypass_vket_lock(self):
        new_date = self.original_date + timedelta(days=3)
        self._create_vket_period(new_date, new_date + timedelta(days=1))
        self.client.force_login(self.superuser)

        response = self._post_date(new_date)

        self.assertRedirects(response, reverse('event:my_list'))
        self.event.refresh_from_db()
        self.assertEqual(self.event.date, new_date)

    def _create_vket_period(
        self, period_start, period_end, *, use_confirmed_match=False, confirmed_date=None,
    ):
        """コラボ期間と本体イベントの紐づけを作る。

        use_confirmed_match=True は本番同様に published_event 未設定で、
        confirmed 日時一致のフォールバック経路を通す。confirmed_date を渡すと
        イベントの現在日と異なる確定日（未移動状態）を再現できる。
        """
        collaboration = VketCollaboration.objects.create(
            slug=f'event-date-lock-{period_start.isoformat()}',
            name='Vket日付変更ロック',
            period_start=period_start,
            period_end=period_end,
            registration_deadline=period_start,
            lt_deadline=period_end,
        )
        link = (
            {
                'confirmed_date': confirmed_date or self.event.date,
                'confirmed_start_time': self.event.start_time,
                'confirmed_duration': self.event.duration,
            }
            if use_confirmed_match
            else {'published_event': self.event}
        )
        return VketParticipation.objects.create(
            collaboration=collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
            **link,
        )

    def _make_recurring_master(self):
        rule = RecurrenceRule.objects.create(
            community=self.community,
            frequency='WEEKLY',
        )
        return Event.objects.create(
            community=self.community,
            date=self.today,
            start_time=time(22, 0),
            duration=60,
            is_recurring_master=True,
            recurrence_rule=rule,
        )
