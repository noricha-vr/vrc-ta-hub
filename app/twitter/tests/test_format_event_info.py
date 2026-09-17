"""format_event_info の日付整形テスト。"""

import datetime

from django.test import TestCase, tag

from tests.factories import make_community, make_event, make_user
from twitter.utils import format_event_info


@tag('offline_external_api')
class FormatEventInfoDateTest(TestCase):
    """告知用イベント情報の date フィールドを検証する。"""

    def setUp(self):
        owner = make_user(
            user_name="format_event_info_owner",
            email="format_event_info_owner@example.com",
            password="testpassword",
        )
        self.community = make_community(
            name="Format Event Info Community",
            owner=owner,
            start_time=datetime.time(22, 0),
            duration=60,
            twitter_hashtag="TestMeetup",
        )

    def test_date_omits_year_and_zero_padding(self):
        """告知は直前に出すため date は年なし・ゼロ埋めなしの「M月D日(曜)」"""
        event = make_event(
            self.community,
            event_date=datetime.date(2099, 9, 13),  # 日曜
            start_time=datetime.time(22, 0),
            duration=60,
        )

        self.assertEqual(format_event_info(event)["date"], "9月13日(日)")

    def test_date_keeps_two_digit_month_and_day(self):
        """2桁の月日はそのまま出る"""
        event = make_event(
            self.community,
            event_date=datetime.date(2099, 12, 25),  # 金曜
            start_time=datetime.time(22, 0),
            duration=60,
        )

        self.assertEqual(format_event_info(event)["date"], "12月25日(金)")
