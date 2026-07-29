"""Event.weekday の書込境界と正規化コマンドを検証する。"""

from datetime import date, time, timedelta
from io import StringIO
from queue import Empty, Queue
from threading import Event as ThreadEvent
from threading import Thread
from unittest import skipUnless

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.db import (
    DatabaseError,
    close_old_connections,
    connection,
    transaction,
)
from django.test import (
    Client,
    RequestFactory,
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
from event.models import Event, RecurrenceRule
from event.recurrence.persistence import create_recurring_events
from event.services.recurrence_override import move_event_occurrence
from event.tests.tweet_generation import TweetGenerationPatchMixin
from ta_hub.index_cache import (
    build_index_database_context,
    get_index_view_cache_key,
)
from utils.vrchat_time import get_vrchat_today


User = get_user_model()
_KEYSET_TEST_EVENT_COUNT = 1001
_THREAD_TIMEOUT_SECONDS = 10
_LOCK_WAIT_OBSERVATION_SECONDS = 0.25
_FAILURE_TRIGGER_NAME = 'test_event_weekday_batch_failure'


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
        event_date = date(2026, 8, 3)
        values = (
            'Mon',
            'Tue',
            'mon',
            'MON',
            'tue',
            'TUE',
            '月曜日',
            '火曜日',
            'Other',
            '',
            ' ',
            'Xday',
        )
        return [
            self._create_event(
                event_date,
                value,
                start_hour=start_hour,
            )
            for start_hour, value in enumerate(values)
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

        self.assertIn('format_only: 3', stdout.getvalue())
        self.assertIn('valid_mismatch: 1', stdout.getvalue())
        self.assertIn('outside_choices: 5', stdout.getvalue())
        self.assertIn('other: 1', stdout.getvalue())
        self.assertIn('empty: 1', stdout.getvalue())
        self.assertIn('total_mismatches: 11', stdout.getvalue())
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

        self.assertIn('changed: 11', first_apply.getvalue())
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

    def test_apply_invalidates_stale_index_cache(self):
        today = get_vrchat_today()
        self.community.poster_image = 'poster/weekday-cache.png'
        self.community.save(update_fields=['poster_image'])
        event = self._create_event(today, 'Other')
        cache_key = get_index_view_cache_key(today)
        self.addCleanup(cache.delete, cache_key)
        request = RequestFactory().get('/')

        cached = build_index_database_context(request, today, cache_key)
        self.assertEqual(cached['upcoming_events'][0]['weekday'], 'Other')

        call_command('normalize_event_weekdays', '--apply')

        self.assertIsNone(cache.get(cache_key))
        rebuilt = build_index_database_context(request, today, cache_key)
        self.assertEqual(
            rebuilt['upcoming_events'][0]['weekday'],
            weekday_code(event.date),
        )

    def test_requires_exactly_one_mode(self):
        with self.assertRaises(CommandError):
            call_command('normalize_event_weekdays')
        with self.assertRaises(CommandError):
            call_command(
                'normalize_event_weekdays',
                '--check',
                '--apply',
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


@tag('offline_external_api')
@skipUnless(connection.vendor == 'mysql', 'MySQL trigger is required')
class NormalizeEventWeekdaysBatchCommitTests(
    TweetGenerationPatchMixin,
    TransactionTestCase,
):
    """失敗済みバッチを再実行で収束させる公開契約を検証する。"""

    def setUp(self) -> None:
        community = Community.objects.create(
            name='曜日バッチ確定テスト集会',
            frequency='不定期',
        )
        start_date = date(2026, 1, 1)
        Event.objects.bulk_create(
            [
                Event(
                    community=community,
                    date=start_date + timedelta(days=offset),
                    start_time=time(21, 0),
                    weekday='Other',
                )
                for offset in range(_KEYSET_TEST_EVENT_COUNT)
            ]
        )
        self.event_pks = list(
            Event.objects.order_by('pk').values_list('pk', flat=True)
        )

    def _create_failure_trigger(self) -> None:
        failed_pk = self.event_pks[-1]
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                CREATE TRIGGER `{_FAILURE_TRIGGER_NAME}`
                BEFORE UPDATE ON `event`
                FOR EACH ROW
                BEGIN
                    IF NEW.id = {failed_pk} THEN
                        SIGNAL SQLSTATE '45000'
                        SET MESSAGE_TEXT = 'forced batch failure';
                    END IF;
                END
                """
            )

    def _drop_failure_trigger(self) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                f'DROP TRIGGER IF EXISTS `{_FAILURE_TRIGGER_NAME}`'
            )

    def test_apply_commits_each_batch_and_retry_converges(self):
        cache_key = get_index_view_cache_key()
        cache.set(cache_key, {'weekday': 'Other'})
        self._create_failure_trigger()
        try:
            with self.assertRaises(DatabaseError):
                call_command('normalize_event_weekdays', '--apply')
        finally:
            self._drop_failure_trigger()
        self.assertIsNone(cache.get(cache_key))

        first_batch = Event.objects.filter(
            pk__lte=self.event_pks[-2],
        )
        for event in first_batch:
            self.assertEqual(event.weekday, weekday_code(event.date))
        failed_event = Event.objects.get(pk=self.event_pks[-1])
        self.assertEqual(failed_event.weekday, 'Other')

        retry = StringIO()
        call_command(
            'normalize_event_weekdays',
            '--apply',
            stdout=retry,
        )
        self.assertIn('changed: 1', retry.getvalue())
        check = StringIO()
        call_command(
            'normalize_event_weekdays',
            '--check',
            stdout=check,
        )
        self.assertIn('total_mismatches: 0', check.getvalue())
