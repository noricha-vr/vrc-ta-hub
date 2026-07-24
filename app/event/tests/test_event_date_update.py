"""Event開催日変更の振る舞いを検証する。"""

from datetime import datetime, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format, time_format

from community.models import Community, CommunityMember
from event.models import Event, EventOccurrenceTombstone, RecurrenceRule
from event.tests.tweet_generation import TweetGenerationPatchMixin
from event_calendar.calendar_utils import generate_google_calendar_url
from ta_hub.index_cache import get_index_view_cache_key
from twitter.models import TweetQueue
from twitter.scheduling import scheduled_at_for_date
from vket.models import VketCollaboration, VketParticipation


User = get_user_model()


class EventDateUpdateViewTests(TweetGenerationPatchMixin, TestCase):
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
        self.today = timezone.localdate()
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
            new_date.strftime('%a'),
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

    @patch('event.views.crud.GoogleCalendarService')
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

    def test_non_member_cannot_update(self):
        self.client.force_login(self.other_user)

        response = self._post_date(self.original_date + timedelta(days=1))

        self.assertRedirects(response, reverse('event:my_list'))
        self.event.refresh_from_db()
        self.assertEqual(self.event.date, self.original_date)

    @patch('event.views.crud.GoogleCalendarService')
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

    @patch('event.views.crud.GoogleCalendarService')
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

    def test_unposted_daily_reminder_is_rescheduled(self):
        queue = TweetQueue.objects.create(
            tweet_type='daily_reminder',
            community=self.community,
            event=self.event,
            status='ready',
            scheduled_at=scheduled_at_for_date(self.original_date),
        )
        new_date = self.original_date + timedelta(days=1)
        self.client.force_login(self.owner)

        self._post_date(new_date)

        queue.refresh_from_db()
        self.assertEqual(queue.scheduled_at, scheduled_at_for_date(new_date))
        self.assertEqual(queue.status, 'ready')

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
        self.event.date = self.today - timedelta(days=2)
        self.event.save(update_fields=['date'])
        queue = TweetQueue.objects.create(
            tweet_type='daily_reminder',
            community=self.community,
            event=self.event,
            status='ready',
            scheduled_at=scheduled_at_for_date(self.event.date),
        )
        self.client.force_login(self.owner)
        after_schedule = timezone.make_aware(
            datetime.combine(self.today, time(20, 0)),
            timezone.get_current_timezone(),
        )

        with patch(
            'event.services.recurrence_override.timezone.now',
            return_value=after_schedule,
        ):
            self._post_date(self.today)

        queue.refresh_from_db()
        expected_schedule = scheduled_at_for_date(self.today)
        self.assertEqual(queue.scheduled_at, expected_schedule)
        self.assertEqual(queue.status, 'skipped')

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

    def test_superuser_can_bypass_vket_lock(self):
        new_date = self.original_date + timedelta(days=3)
        self._create_vket_period(new_date, new_date + timedelta(days=1))
        self.client.force_login(self.superuser)

        response = self._post_date(new_date)

        self.assertRedirects(response, reverse('event:my_list'))
        self.event.refresh_from_db()
        self.assertEqual(self.event.date, new_date)

    def _create_vket_period(self, period_start, period_end):
        collaboration = VketCollaboration.objects.create(
            slug=f'event-date-lock-{period_start.isoformat()}',
            name='Vket日付変更ロック',
            period_start=period_start,
            period_end=period_end,
            registration_deadline=period_start,
            lt_deadline=period_end,
        )
        return VketParticipation.objects.create(
            collaboration=collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
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
