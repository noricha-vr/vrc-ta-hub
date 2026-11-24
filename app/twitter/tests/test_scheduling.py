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

    def test_same_day_presentation_after_slot_stays_today(self):
        """当日 12:00 以降に承認された lt/special は翌日に送らず即時時刻を返す"""
        event = MagicMock(date=datetime.date(2026, 4, 20))

        with timezone.override("Asia/Tokyo"):
            before_noon = timezone.make_aware(datetime.datetime(2026, 4, 20, 10, 0))
            self.assert_scheduled_local(
                default_scheduled_at("lt", event=event, base_datetime=before_noon),
                datetime.date(2026, 4, 20),
                12,
            )

            after_noon = timezone.make_aware(datetime.datetime(2026, 4, 20, 15, 30))
            for tweet_type in ("lt", "special"):
                self.assertEqual(
                    default_scheduled_at(tweet_type, event=event, base_datetime=after_noon),
                    after_noon,
                )

    def test_morning_event_clamps_schedule_before_start_time(self):
        """午前開催のイベントで 12:00 枠が開演後になる場合は開演前へ寄せる"""
        with timezone.override("Asia/Tokyo"):
            same_day_morning = MagicMock(
                date=datetime.date(2026, 4, 20),
                start_time=datetime.time(11, 0),
            )
            at_10 = timezone.make_aware(datetime.datetime(2026, 4, 20, 10, 0))
            self.assertEqual(
                default_scheduled_at("lt", event=same_day_morning, base_datetime=at_10),
                at_10,
            )

            next_day_morning = MagicMock(
                date=datetime.date(2026, 4, 21),
                start_time=datetime.time(11, 0),
            )
            prev_afternoon = timezone.make_aware(datetime.datetime(2026, 4, 20, 15, 0))
            expected = timezone.make_aware(datetime.datetime(2026, 4, 21, 9, 0))
            self.assertEqual(
                default_scheduled_at("special", event=next_day_morning, base_datetime=prev_afternoon),
                expected,
            )
