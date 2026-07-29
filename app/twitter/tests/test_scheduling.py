"""投稿予約時刻のテスト。"""

import datetime
from unittest.mock import MagicMock

from django.test import TestCase, tag
from django.utils import timezone

from twitter.scheduling import default_scheduled_at

@tag('offline_external_api')
class TweetSchedulingTest(TestCase):
    """tweet_type ごとのデフォルト予約時刻のテスト"""

    def assert_scheduled_local(self, scheduled_at, expected_date, expected_hour):
        local_scheduled_at = timezone.localtime(scheduled_at)
        self.assertEqual(local_scheduled_at.date(), expected_date)
        self.assertEqual(local_scheduled_at.hour, expected_hour)
        self.assertEqual(local_scheduled_at.minute, 0)

    def test_default_scheduled_at_uses_type_specific_hours(self):
        """告知種別ごとに 10/12/19 時の予約枠を使う"""
        with timezone.override("Asia/Tokyo"):
            base = timezone.make_aware(datetime.datetime(2026, 4, 20, 9, 0))
            expected_date = datetime.date(2026, 4, 20)

            self.assert_scheduled_local(
                default_scheduled_at("slide_share", base_datetime=base),
                expected_date,
                10,
            )
            self.assert_scheduled_local(
                default_scheduled_at("new_community", base_datetime=base),
                expected_date,
                12,
            )
            self.assert_scheduled_local(
                default_scheduled_at("lt", base_datetime=base),
                expected_date,
                12,
            )
            self.assert_scheduled_local(
                default_scheduled_at("special", base_datetime=base),
                expected_date,
                12,
            )

    def test_default_scheduled_at_moves_to_next_day_after_slot(self):
        """当日の予約枠を過ぎていたら翌日の同時刻にする"""
        with timezone.override("Asia/Tokyo"):
            base = timezone.make_aware(datetime.datetime(2026, 4, 20, 12, 1))
            self.assert_scheduled_local(
                default_scheduled_at("lt", base_datetime=base),
                datetime.date(2026, 4, 21),
                12,
            )

    def test_daily_reminder_uses_event_date_at_19(self):
        """当日リマインドはイベント当日19時のままにする"""
        event = MagicMock(date=datetime.date(2026, 4, 25))

        with timezone.override("Asia/Tokyo"):
            base = timezone.make_aware(datetime.datetime(2026, 4, 20, 23, 0))
            self.assert_scheduled_local(
                default_scheduled_at("daily_reminder", event=event, base_datetime=base),
                datetime.date(2026, 4, 25),
                19,
            )
