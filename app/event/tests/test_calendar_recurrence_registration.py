"""登録画面からの周期保存と後続生成を実DBで検証する。"""
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from event.forms import GoogleCalendarEventForm
from event.models import Event, EventOccurrenceTombstone, RecurrenceRule
from event.recurrence import RecurrenceService
from tests.factories import make_community, make_user


class CalendarRecurrenceRegistrationTest(TestCase):
    def setUp(self):
        self.user = make_user()
        self.community = make_community(owner=self.user, frequency='未設定', weekdays=[])
        self.client.force_login(self.user)
        session = self.client.session
        session['active_community_id'] = self.community.pk
        session.save()
        self.url = reverse('event:calendar_create')
        self.start = timezone.localdate() + timedelta(days=10)

    def payload(self, kind='weekly', start=None, **extra):
        start = start or self.start
        return {
            'start_date': start.isoformat(), 'start_time': '20:30', 'duration': 90,
            'recurrence_type': kind,
            'weekday': ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU'][start.weekday()],
            'monthly_day': start.day, 'week_number': (start.day - 1) // 7 + 1,
            **extra,
        }

    def test_all_supported_cycles_create_rules_and_expected_dates(self):
        for kind, frequency, interval in [
            ('weekly', 'WEEKLY', 1), ('biweekly', 'WEEKLY', 2),
            ('monthly_by_date', 'MONTHLY_BY_DATE', 1),
            ('monthly_by_day', 'MONTHLY_BY_WEEK', 1),
        ]:
            with self.subTest(kind=kind):
                start = self.start.replace(day=15)
                response = self.client.post(self.url, self.payload(kind, start))
                self.assertEqual(response.status_code, 302)
                rule = RecurrenceRule.objects.get(community=self.community)
                self.assertEqual((rule.frequency, rule.interval, rule.start_date), (frequency, interval, start))
                events = list(Event.objects.filter(community=self.community).order_by('date'))
                self.assertGreater(len(events), 1)
                self.assertEqual(events[0].date, start)
                self.assertTrue(events[0].is_recurring_master)
                self.assertTrue(all(event.recurring_master_id == events[0].pk for event in events[1:]))
                self.assertTrue(all((event.start_time, event.duration) == (time(20, 30), 90) for event in events))
                if kind in ('weekly', 'biweekly'):
                    self.assertTrue(all((event.date - start).days % (7 * interval) == 0 for event in events))
                elif kind == 'monthly_by_date':
                    self.assertTrue(all(event.date.day == 15 for event in events))
                else:
                    self.assertTrue(all(event.date.weekday() == start.weekday() and 15 <= event.date.day <= 21 for event in events))
                self.community.refresh_from_db()
                self.assertEqual(self.community.start_time, time(20, 30))
                self.assertEqual(self.community.duration, 90)
                self.assertNotEqual(self.community.frequency, '未設定')
                if kind == 'monthly_by_date':
                    self.assertEqual(self.community.weekdays, [])
                # 同一POSTを再送信しても新規ルール・イベントを作らない。
                response = self.client.post(self.url, self.payload(kind, start))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(RecurrenceRule.objects.count(), 1)
                self.assertEqual(Event.objects.count(), len(events))
                Event.objects.all().delete()
                rule.delete(delete_future_events=False)

    def test_single_event_has_no_rule(self):
        response = self.client.post(self.url, self.payload('none'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(RecurrenceRule.objects.count(), 0)
        self.community.refresh_from_db()
        self.assertEqual(self.community.frequency, '単発')

    def test_last_weekday(self):
        start = self.start.replace(day=28)
        # 28日の曜日がその月の最終回になるよう月末まで進める。
        while (start + timedelta(days=7)).month == start.month:
            start += timedelta(days=7)
        response = self.client.post(self.url, self.payload('monthly_by_day', start, week_number=-1))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RecurrenceRule.objects.get().week_of_month, -1)
        self.community.refresh_from_db()
        self.assertIn('毎月最終', self.community.frequency)
        self.assertEqual(self.community.frequency.count('曜日'), 1)
        for event in Event.objects.all():
            self.assertEqual(event.date.weekday(), start.weekday())
            self.assertNotEqual((event.date + timedelta(days=7)).month, event.date.month)

    def test_schedule_mismatches_are_rejected(self):
        start = self.start.replace(day=15)
        for data in [
            self.payload('weekly', start, weekday=['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU'][(start.weekday()+1)%7]),
            self.payload('monthly_by_date', start, monthly_day=16),
            self.payload('monthly_by_day', start, week_number=1),
        ]:
            response = self.client.post(self.url, data)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['form'].errors)
        self.assertEqual(Event.objects.count(), 0)
        self.assertEqual(RecurrenceRule.objects.count(), 0)

    def test_generation_failure_rolls_back_everything(self):
        original = RecurrenceService.create_recurring_events
        def fail_after_writes(service, *args, **kwargs):
            original(service, *args, **kwargs)
            raise RuntimeError('injected failure after persistence')
        for result in ('empty', 'exception'):
            with self.subTest(result=result):
                options = {'return_value': []} if result == 'empty' else {'autospec': True, 'side_effect': fail_after_writes}
                with patch.object(RecurrenceService, 'create_recurring_events', **options):
                    response = self.client.post(self.url, self.payload())
                self.assertEqual(response.status_code, 200)
                self.assertEqual(Event.objects.count(), 0)
                self.assertEqual(RecurrenceRule.objects.count(), 0)
                self.community.refresh_from_db()
                self.assertEqual(self.community.frequency, '未設定')

    def test_deleted_first_date_does_not_silently_shift_registration(self):
        EventOccurrenceTombstone.objects.create(
            community=self.community, date=self.start, original_start_time=time(20, 30), reason='deleted')
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Event.objects.count(), 0)
        self.assertEqual(RecurrenceRule.objects.count(), 0)

    def test_closed_community_cannot_register(self):
        self.community.end_at = timezone.localdate()
        self.community.save()
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Event.objects.count(), 0)

    def test_monthly_continuation_keeps_day_and_follows_community_time(self):
        start = self.start.replace(day=15)
        self.client.post(self.url, self.payload('monthly_by_date', start))
        last = Event.objects.latest('date').date
        self.community.start_time = time(23, 0)
        self.community.duration = 30
        self.community.save()
        now = datetime.combine(last + timedelta(days=1), time(0), tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now', return_value=now):
            call_command('generate_recurring_events', months=3, stdout=StringIO())
            count = Event.objects.count()
            call_command('generate_recurring_events', months=3, stdout=StringIO())
            self.assertEqual(Event.objects.count(), count)
        added = Event.objects.filter(date__gt=last)
        self.assertTrue(added.exists())
        for event in added:
            self.assertEqual(event.date.day, 15)
            self.assertEqual((event.start_time, event.duration), (time(23, 0), 30))

    def test_month_end_limit_remains(self):
        form = GoogleCalendarEventForm(self.payload('monthly_by_date', date(2026, 10, 31)))
        self.assertFalse(form.is_valid())
        self.assertIn('monthly_day', form.errors)

    def test_single_addition_preserves_existing_recurring_schedule(self):
        self.client.post(self.url, self.payload())
        self.community.refresh_from_db()
        before = (self.community.frequency, self.community.weekdays,
                  self.community.start_time, self.community.duration)
        original_count = Event.objects.count()
        response = self.client.post(self.url, self.payload(
            'none', self.start + timedelta(days=1), start_time='19:00', duration=30))
        self.assertEqual(response.status_code, 302)
        self.community.refresh_from_db()
        self.assertEqual((self.community.frequency, self.community.weekdays,
                          self.community.start_time, self.community.duration), before)
        self.assertEqual(RecurrenceRule.objects.count(), 1)
        self.assertEqual(Event.objects.count(), original_count + 1)
