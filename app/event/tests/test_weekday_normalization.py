"""Event.weekday の書込境界と正規化コマンドを検証する。"""

from datetime import date, time, timedelta
from io import StringIO
from queue import Empty, Queue
from threading import Event as ThreadEvent
from threading import Thread

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.db import close_old_connections, transaction
from django.test import (
    Client,
    RequestFactory,
    SimpleTestCase,
    TestCase,
    TransactionTestCase,
    skipUnlessDBFeature,
    tag,
)
from django.urls import reverse
from django.utils import timezone

from community.constants import weekday_code
from community.models import Community, CommunityMember
from event.admin import EventAdmin
from event.management.commands.normalize_event_weekdays import (
    _classify_weekday,
)
from event.models import Event, RecurrenceRule
from event.recurrence.persistence import create_recurring_events
from event.services.recurrence_override import move_event_occurrence
from event.tests.tweet_generation import TweetGenerationPatchMixin


User = get_user_model()
_KEYSET_TEST_EVENT_COUNT = 1001
_THREAD_TIMEOUT_SECONDS = 10
_LOCK_WAIT_OBSERVATION_SECONDS = 0.25


class WeekdayClassificationTests(SimpleTestCase):
    """旧値を日付との関係に応じて分類する。"""

    def test_classifies_supported_and_invalid_values(self):
        cases = (
            ('canonical match', 'Mon', 'Mon', None),
            ('canonical mismatch', 'Tue', 'Mon', 'valid_mismatch'),
            ('lowercase alias match', 'mon', 'Mon', 'format_only'),
            ('uppercase alias match', 'MON', 'Mon', 'format_only'),
            ('lowercase alias mismatch', 'tue', 'Mon', 'outside_choices'),
            ('uppercase alias mismatch', 'TUE', 'Mon', 'outside_choices'),
            ('Japanese alias match', '月曜日', 'Mon', 'format_only'),
            ('Japanese alias mismatch', '火曜日', 'Mon', 'outside_choices'),
            ('Other', 'Other', 'Mon', 'other'),
            ('empty', '', 'Mon', 'empty'),
            ('whitespace', ' ', 'Mon', 'outside_choices'),
            ('unknown', 'Funday', 'Mon', 'outside_choices'),
        )

        for label, value, expected, category in cases:
            with self.subTest(label=label):
                self.assertEqual(
                    _classify_weekday(value, expected),
                    category,
                )


@tag('offline_external_api')
class EventWeekdayWriterTests(TweetGenerationPatchMixin, TestCase):
    """Event を保存する各境界が date 由来の曜日を使うことを検証する。"""

    def setUp(self) -> None:
        self.community = Community.objects.create(
            name='曜日書込テスト集会',
            status='approved',
            weekdays=['Mon'],
            frequency='毎週',
            start_time=time(21, 0),
            duration=60,
        )

    def test_calendar_create_uses_event_date(self):
        user = User.objects.create_user(
            user_name='weekday_calendar_owner',
            email='weekday-calendar@example.com',
            password='testpass123',
        )
        CommunityMember.objects.create(
            community=self.community,
            user=user,
            role=CommunityMember.Role.OWNER,
        )
        client = Client()
        client.force_login(user)
        session = client.session
        session['active_community_id'] = self.community.pk
        session.save()
        event_date = timezone.localdate() + timedelta(days=10)

        response = client.post(
            reverse('event:calendar_create'),
            {
                'start_date': event_date.isoformat(),
                'start_time': '21:00',
                'duration': 60,
                'recurrence_type': 'none',
            },
        )

        self.assertRedirects(response, reverse('event:my_list'))
        event = Event.objects.get(community=self.community, date=event_date)
        self.assertEqual(event.weekday, weekday_code(event_date))

    def test_recurrence_persistence_uses_each_event_date(self):
        rule = RecurrenceRule.objects.create(
            community=self.community,
            frequency='WEEKLY',
        )
        dates = [date(2026, 8, 3), date(2026, 8, 11)]

        events = create_recurring_events(
            self.community,
            rule,
            dates,
            time(21, 0),
            60,
        )

        self.assertEqual(
            [event.weekday for event in events],
            ['Mon', 'Tue'],
        )

    def test_recurrence_override_replaces_legacy_weekday(self):
        original_date = date(2026, 8, 3)
        event = Event.objects.create(
            community=self.community,
            date=original_date,
            start_time=time(21, 0),
            weekday='MON',
        )
        new_date = date(2026, 8, 5)

        move_event_occurrence(event, new_date)

        event.refresh_from_db()
        self.assertEqual(event.date, new_date)
        self.assertEqual(event.weekday, 'Wed')

    def test_admin_makes_weekday_readonly_and_derives_it_on_save(self):
        event_admin = EventAdmin(Event, admin.site)
        event = Event(
            community=self.community,
            date=date(2026, 8, 6),
            start_time=time(21, 0),
            weekday='Other',
        )
        request = RequestFactory().post('/admin/event/event/add/')

        self.assertIn(
            'weekday',
            event_admin.get_readonly_fields(request, event),
        )
        event_admin.save_model(request, event, form=None, change=False)

        event.refresh_from_db()
        self.assertEqual(event.weekday, 'Thu')


@tag('offline_external_api')
class NormalizeEventWeekdaysCommandTests(TweetGenerationPatchMixin, TestCase):
    """正規化コマンドの分類、適用、冪等性を検証する。"""

    def setUp(self) -> None:
        self.community = Community.objects.create(
            name='曜日正規化テスト集会',
            status='approved',
            weekdays=['Other'],
            frequency='不定期',
            start_time=time(21, 0),
            duration=60,
        )

    def _create_event(
        self,
        event_date: date,
        weekday: str,
        *,
        start_hour: int = 21,
    ) -> Event:
        return Event.objects.create(
            community=self.community,
            date=event_date,
            start_time=time(start_hour, 0),
            weekday=weekday,
        )

    def _create_classification_examples(self) -> list[Event]:
        return [
            self._create_event(date(2026, 8, 3), 'Mon'),
            self._create_event(
                date(2026, 8, 3),
                'MON',
                start_hour=22,
            ),
            self._create_event(date(2026, 8, 5), '水曜日'),
            self._create_event(date(2026, 8, 7), 'Thu'),
            self._create_event(date(2026, 8, 8), '???'),
            self._create_event(date(2026, 8, 9), 'Other'),
            self._create_event(date(2026, 8, 10), ''),
        ]

    def test_check_classifies_without_writing_and_returns_nonzero(self):
        events = self._create_classification_examples()
        original_values = {
            event.pk: event.weekday
            for event in events
        }
        stdout = StringIO()

        with self.assertRaises(CommandError):
            call_command(
                'normalize_event_weekdays',
                '--check',
                stdout=stdout,
            )

        self.assertIn('format_only: 2', stdout.getvalue())
        self.assertIn('valid_mismatch: 1', stdout.getvalue())
        self.assertIn('outside_choices: 1', stdout.getvalue())
        self.assertIn('other: 1', stdout.getvalue())
        self.assertIn('empty: 1', stdout.getvalue())
        self.assertIn('total_mismatches: 6', stdout.getvalue())
        current_values = dict(
            Event.objects.values_list('pk', 'weekday')
        )
        self.assertEqual(current_values, original_values)
        self.community.refresh_from_db()
        self.assertEqual(self.community.weekdays, ['Other'])

    def test_apply_normalizes_all_rows_and_second_check_is_clean(self):
        self._create_classification_examples()
        first_apply = StringIO()

        call_command(
            'normalize_event_weekdays',
            '--apply',
            stdout=first_apply,
        )

        self.assertIn('changed: 6', first_apply.getvalue())
        for event in Event.objects.all():
            self.assertEqual(event.weekday, weekday_code(event.date))

        second_apply = StringIO()
        call_command(
            'normalize_event_weekdays',
            '--apply',
            stdout=second_apply,
        )
        self.assertIn('changed: 0', second_apply.getvalue())

        check = StringIO()
        call_command(
            'normalize_event_weekdays',
            '--check',
            stdout=check,
        )
        self.assertIn('total_mismatches: 0', check.getvalue())

    def test_requires_exactly_one_mode(self):
        with self.assertRaises(CommandError):
            call_command('normalize_event_weekdays')
        with self.assertRaises(CommandError):
            call_command(
                'normalize_event_weekdays',
                '--check',
                '--apply',
            )

    def test_apply_normalizes_rows_beyond_first_keyset_batch(self):
        start_date = date(2026, 1, 1)
        Event.objects.bulk_create(
            [
                Event(
                    community=self.community,
                    date=start_date + timedelta(days=offset),
                    start_time=time(21, 0),
                    weekday='Other',
                )
                for offset in range(_KEYSET_TEST_EVENT_COUNT)
            ]
        )
        stdout = StringIO()

        call_command(
            'normalize_event_weekdays',
            '--apply',
            stdout=stdout,
        )

        self.assertIn(
            f'changed: {_KEYSET_TEST_EVENT_COUNT}',
            stdout.getvalue(),
        )
        for event in Event.objects.all():
            self.assertEqual(
                event.weekday,
                weekday_code(event.date),
            )


@tag('offline_external_api')
class NormalizeEventWeekdaysLockingTests(
    TweetGenerationPatchMixin,
    TransactionTestCase,
):
    """apply が競合する日付更新後も曜日整合性を維持する。"""

    def setUp(self) -> None:
        community = Community.objects.create(
            name='曜日ロック境界テスト集会',
            frequency='不定期',
        )
        self.event = Event.objects.create(
            community=community,
            date=date(2026, 8, 3),
            start_time=time(21, 0),
            weekday='TUE',
        )
        self.writer_locked = ThreadEvent()
        self.release_writer = ThreadEvent()
        self.normalizer_started = ThreadEvent()
        self.normalizer_finished = ThreadEvent()
        self.errors: Queue[Exception] = Queue()
        self.writer = Thread(target=self._run_writer)
        self.normalizer = Thread(target=self._run_normalizer)

    def _run_writer(self) -> None:
        close_old_connections()
        try:
            with transaction.atomic():
                event = Event.objects.select_for_update().get(
                    pk=self.event.pk,
                )
                event.date = date(2026, 8, 5)
                event.weekday = weekday_code(event.date)
                event.save(update_fields=['date', 'weekday'])
                self.writer_locked.set()
                if not self.release_writer.wait(_THREAD_TIMEOUT_SECONDS):
                    raise TimeoutError('writer lock release timed out')
        except Exception as exc:
            self.errors.put(exc)
            self.writer_locked.set()
        finally:
            close_old_connections()

    def _run_normalizer(self) -> None:
        close_old_connections()
        self.normalizer_started.set()
        try:
            call_command(
                'normalize_event_weekdays',
                '--apply',
                stdout=StringIO(),
            )
        except Exception as exc:
            self.errors.put(exc)
        finally:
            self.normalizer_finished.set()
            close_old_connections()

    def _drain_errors(self) -> list[Exception]:
        found = []
        while True:
            try:
                found.append(self.errors.get_nowait())
            except Empty:
                return found

    def _release_and_join_threads(self) -> None:
        self.release_writer.set()
        for thread in (self.writer, self.normalizer):
            if thread.ident is not None:
                thread.join(_THREAD_TIMEOUT_SECONDS)

    @skipUnlessDBFeature('has_select_for_update')
    def test_apply_waits_for_concurrent_date_update_and_stays_consistent(self):
        self.writer.start()
        try:
            self.assertTrue(
                self.writer_locked.wait(_THREAD_TIMEOUT_SECONDS),
                'writer did not acquire the row lock',
            )
            self.assertEqual(self._drain_errors(), [])
            self.normalizer.start()
            self.assertTrue(
                self.normalizer_started.wait(_THREAD_TIMEOUT_SECONDS),
                'normalizer did not start',
            )
            self.assertFalse(
                self.normalizer_finished.wait(
                    _LOCK_WAIT_OBSERVATION_SECONDS
                ),
                'normalizer finished while the writer held the row lock',
            )
        finally:
            self._release_and_join_threads()

        self.assertFalse(self.writer.is_alive())
        self.assertFalse(self.normalizer.is_alive())
        self.assertEqual(self._drain_errors(), [])
        self.event.refresh_from_db()
        self.assertEqual(
            self.event.weekday,
            weekday_code(self.event.date),
        )
