"""utils.vrchat_time のテスト"""
from datetime import datetime
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils import timezone

from utils.vrchat_time import get_vrchat_today


class GetVrchatTodayTestCase(SimpleTestCase):
    """get_vrchat_today のテスト

    VRChatter の生活リズムに合わせ、朝4時を日付の境界とする。
    """

    BOUNDARY_HOUR = 4

    @patch("utils.vrchat_time.timezone.now")
    def test_before_boundary_returns_previous_date(self, mock_now):
        """朝4時前は前日として扱う（例: 3:59 -> 前日）"""
        mock_now.return_value = timezone.make_aware(
            datetime(2026, 4, 6, 3, 59, 0)
        )
        result = get_vrchat_today()
        self.assertEqual(result.day, 5)
        self.assertEqual(result.month, 4)
        self.assertEqual(result.year, 2026)

    @patch("utils.vrchat_time.timezone.now")
    def test_at_midnight_returns_previous_date(self, mock_now):
        """午前0時は前日として扱う"""
        mock_now.return_value = timezone.make_aware(
            datetime(2026, 4, 6, 0, 0, 0)
        )
        result = get_vrchat_today()
        self.assertEqual(result.day, 5)

    @patch("utils.vrchat_time.timezone.now")
    def test_at_boundary_returns_current_date(self, mock_now):
        """朝4時ちょうどは当日として扱う"""
        mock_now.return_value = timezone.make_aware(
            datetime(2026, 4, 6, 4, 0, 0)
        )
        result = get_vrchat_today()
        self.assertEqual(result.day, 6)
        self.assertEqual(result.month, 4)

    @patch("utils.vrchat_time.timezone.now")
    def test_after_boundary_returns_current_date(self, mock_now):
        """朝4時以降は当日として扱う（例: 15:00）"""
        mock_now.return_value = timezone.make_aware(
            datetime(2026, 4, 6, 15, 0, 0)
        )
        result = get_vrchat_today()
        self.assertEqual(result.day, 6)

    @patch("utils.vrchat_time.timezone.now")
    def test_late_night_returns_previous_date(self, mock_now):
        """深夜（例: 2:30）は前日として扱う"""
        mock_now.return_value = timezone.make_aware(
            datetime(2026, 4, 6, 2, 30, 0)
        )
        result = get_vrchat_today()
        self.assertEqual(result.day, 5)

    @patch("utils.vrchat_time.timezone.now")
    def test_month_boundary_crossover(self, mock_now):
        """月初の午前0時は前月末日として扱う"""
        mock_now.return_value = timezone.make_aware(
            datetime(2026, 5, 1, 1, 0, 0)
        )
        result = get_vrchat_today()
        self.assertEqual(result.day, 30)
        self.assertEqual(result.month, 4)

    @patch("utils.vrchat_time.timezone.now")
    def test_returns_date_object(self, mock_now):
        """戻り値が date オブジェクトである"""
        from datetime import date

        mock_now.return_value = timezone.make_aware(
            datetime(2026, 4, 6, 12, 0, 0)
        )
        result = get_vrchat_today()
        self.assertIsInstance(result, date)
