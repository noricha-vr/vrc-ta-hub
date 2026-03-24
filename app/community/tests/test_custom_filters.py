from datetime import date

from django.test import TestCase

from community.templatetags.custom_filters import (
    date_weekday_jp,
    weekday_abbr,
    weekday_jp,
)


class WeekdayJpFilterTest(TestCase):
    """weekday_jp フィルターのテスト"""

    def test_all_weekdays(self):
        expected = {
            'Sun': '日曜日', 'Mon': '月曜日', 'Tue': '火曜日', 'Wed': '水曜日',
            'Thu': '木曜日', 'Fri': '金曜日', 'Sat': '土曜日',
        }
        for eng, jp in expected.items():
            self.assertEqual(weekday_jp(eng), jp)

    def test_other(self):
        self.assertEqual(weekday_jp('Other'), 'その他')

    def test_uppercase(self):
        self.assertEqual(weekday_jp('SUN'), '日曜日')
        self.assertEqual(weekday_jp('SAT'), '土曜日')
        self.assertEqual(weekday_jp('MON'), '月曜日')

    def test_lowercase(self):
        self.assertEqual(weekday_jp('sun'), '日曜日')
        self.assertEqual(weekday_jp('sat'), '土曜日')

    def test_unknown_value_fallback(self):
        self.assertEqual(weekday_jp('Unknown'), 'Unknown')

    def test_empty_string(self):
        self.assertEqual(weekday_jp(''), '')


class DateWeekdayJpFilterTest(TestCase):
    """date_weekday_jp フィルターのテスト"""

    def test_saturday(self):
        self.assertEqual(date_weekday_jp(date(2026, 3, 28)), '土曜日')

    def test_tuesday(self):
        self.assertEqual(date_weekday_jp(date(2026, 3, 24)), '火曜日')

    def test_sunday(self):
        self.assertEqual(date_weekday_jp(date(2026, 3, 29)), '日曜日')

    def test_none(self):
        self.assertEqual(date_weekday_jp(None), '')


class WeekdayAbbrFilterTest(TestCase):
    """weekday_abbr フィルターの回帰テスト"""

    def test_all_weekdays(self):
        expected = {
            'Sun': '日', 'Mon': '月', 'Tue': '火', 'Wed': '水',
            'Thu': '木', 'Fri': '金', 'Sat': '土', 'Other': '他',
        }
        for eng, jp in expected.items():
            self.assertEqual(weekday_abbr(eng), jp)

    def test_uppercase(self):
        self.assertEqual(weekday_abbr('SUN'), '日')
        self.assertEqual(weekday_abbr('SAT'), '土')

    def test_unknown_fallback(self):
        self.assertEqual(weekday_abbr('Unknown'), 'Unknown')
