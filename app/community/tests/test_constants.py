"""community.constants の曜日コード変換を検証する。"""

from datetime import date, timedelta

from django.test import SimpleTestCase

from community.constants import weekday_code


class WeekdayCodeTests(SimpleTestCase):
    """ロケールに依存しない曜日コード変換を検証する。"""

    def test_returns_fixed_codes_for_all_weekdays(self):
        monday = date(2026, 8, 3)

        actual = [
            weekday_code(monday + timedelta(days=offset))
            for offset in range(7)
        ]

        self.assertEqual(
            actual,
            ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
        )

    def test_does_not_use_locale_dependent_strftime(self):
        class DateWithoutStrftime(date):
            def strftime(self, _format):
                raise AssertionError('strftime must not be used')

        value = DateWithoutStrftime(2026, 8, 3)

        self.assertEqual(weekday_code(value), 'Mon')
