"""曜日正規化とキャッシュ破棄が同時に失敗する境界を検証する。"""

from datetime import date, time
from unittest import skipUnless
from unittest.mock import patch

from django.core.management import call_command
from django.db import DatabaseError, connection
from django.test import TestCase, TransactionTestCase, tag

from community.models import Community
from event.models import Event
from event.tests.tweet_generation import TweetGenerationPatchMixin


_FAILURE_TRIGGER_NAME = 'test_event_weekday_db_cache_failure'
_CACHE_FAILURE_DETAIL = 'SENSITIVE_CACHE_DETAIL'
_COMMAND_LOGGER = 'event.management.commands.normalize_event_weekdays'


@tag('offline_external_api')
class NormalizeEventWeekdayCacheFailureTests(TestCase):
    """正常なapply後はキャッシュ破棄失敗を呼出元へ通知する。"""

    def test_keyboard_interrupt_survives_cache_clear_error(self) -> None:
        with self.assertLogs(_COMMAND_LOGGER, level='ERROR') as captured:
            with patch(
                'event.management.commands.normalize_event_weekdays.Command._apply',
                side_effect=KeyboardInterrupt,
            ):
                with patch(
                    'ta_hub.index_cache.cache.delete',
                    side_effect=RuntimeError(_CACHE_FAILURE_DETAIL),
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        call_command('normalize_event_weekdays', '--apply')

        logs = '\n'.join(captured.output)
        self.assertIn('キャッシュ破棄にも失敗しました', logs)
        self.assertNotIn(_CACHE_FAILURE_DETAIL, logs)

    def test_successful_apply_propagates_cache_clear_failure(self) -> None:
        with patch(
            'ta_hub.index_cache.cache.delete',
            side_effect=RuntimeError('cache unavailable'),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                'cache unavailable',
            ):
                call_command('normalize_event_weekdays', '--apply')


@tag('offline_external_api')
@skipUnless(connection.vendor == 'mysql', 'MySQL trigger is required')
class NormalizeEventWeekdayDualFailureTests(
    TweetGenerationPatchMixin,
    TransactionTestCase,
):
    """DB失敗をキャッシュ破棄失敗より優先して通知する。"""

    def setUp(self) -> None:
        community = Community.objects.create(
            name='曜日DB・キャッシュ同時失敗テスト集会',
            frequency='不定期',
        )
        self.event = Event.objects.create(
            community=community,
            date=date(2026, 8, 3),
            start_time=time(21, 0),
            weekday='Other',
        )

    def _create_failure_trigger(self) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                CREATE TRIGGER `{_FAILURE_TRIGGER_NAME}`
                BEFORE UPDATE ON `event`
                FOR EACH ROW
                BEGIN
                    IF NEW.id = {self.event.pk} THEN
                        SIGNAL SQLSTATE '45000'
                        SET MESSAGE_TEXT = 'forced database failure';
                    END IF;
                END
                """
            )

    def _drop_failure_trigger(self) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                f'DROP TRIGGER IF EXISTS `{_FAILURE_TRIGGER_NAME}`'
            )

    def test_database_error_survives_cache_clear_error(self) -> None:
        self._create_failure_trigger()
        self.addCleanup(self._drop_failure_trigger)

        with self.assertLogs(_COMMAND_LOGGER, level='ERROR') as captured:
            with patch(
                'ta_hub.index_cache.cache.delete',
                side_effect=RuntimeError(_CACHE_FAILURE_DETAIL),
            ):
                with self.assertRaises(DatabaseError) as raised:
                    call_command('normalize_event_weekdays', '--apply')

        self.assertIn('forced database failure', str(raised.exception))
        logs = '\n'.join(captured.output)
        self.assertIn('キャッシュ破棄にも失敗しました', logs)
        self.assertNotIn(_CACHE_FAILURE_DETAIL, logs)
