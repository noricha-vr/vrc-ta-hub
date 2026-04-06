from datetime import datetime, date
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils import timezone

from utils.vrchat_time import get_vrchat_today


class GetVrchatTodayTest(SimpleTestCase):
    def test_returns_previous_date_before_4am(self):
        mocked_now = timezone.make_aware(datetime(2026, 4, 6, 3, 59))

        with patch("utils.vrchat_time.timezone.now", return_value=mocked_now):
            self.assertEqual(get_vrchat_today(), date(2026, 4, 5))

    def test_returns_current_date_at_or_after_4am(self):
        mocked_now = timezone.make_aware(datetime(2026, 4, 6, 4, 0))

        with patch("utils.vrchat_time.timezone.now", return_value=mocked_now):
            self.assertEqual(get_vrchat_today(), date(2026, 4, 6))
