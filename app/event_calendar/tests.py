# app/event_calendar/tests.py

import datetime
from urllib.parse import quote_plus

from django.test import TestCase
from django.utils import timezone

from account.models import CustomUser
from community.models import Community
from event.models import Event
from .calendar_utils import create_calendar_entry_url, PLATFORM_MAP
from .models import CalendarEntry


class CreateCalendarTest(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            user_name="TestUser",
            email="test@example.com",
            password="testpassword"
        )
        self.community = Community.objects.create(
            custom_user=self.user,
            name="Test Community",
            start_time=datetime.time(21, 0),
            duration=60,
            weekdays=["Mon", "Wed", "Fri"],
            frequency="Weekly",
            organizers="Test Organizer",
            description="Test Description",
            platform="All"
        )
        self.event = Event.objects.create(
            community=self.community,
            date=timezone.now().date() + datetime.timedelta(days=1),  # 明日の日付を使用
            start_time=datetime.time(21, 0),
            duration=60,
            weekday="Mon"
        )

    def test_create_calendar_entry_url(self):
        calendar_entry = CalendarEntry.objects.create(
            event=self.event,
            join_condition="誰でも参加可能",
            event_detail="This is a test event for VRC Calendar. Location: VRChat",
            how_to_join="VRChatで「Test Event」を検索してください。",
            note="Tags: test, vrc, calendar. URL: https://example.com",
            is_overseas_user=False,
            event_genres=["OTHER_MEETUP", "VR_DRINKING_PARTY"]
        )

        url = create_calendar_entry_url(calendar_entry)

        # URLの基本構造を確認
        self.assertTrue(url.startswith(
            'https://docs.google.com/forms/d/e/1FAIpQLSevo0ax6ALIzllRCT7up-3KZkohD3VfG28rcOy8XMqDwRWevQ/formResponse?'))

        # 重要なパラメータの存在を確認
        self.assertIn('entry.426573786=Test+Community', url)  # イベント名
        self.assertIn(f'entry.450203369_year={self.event.date.year}', url)  # 年
        self.assertIn(f'entry.450203369_month={self.event.date.month:02d}', url)  # 月
        self.assertIn(f'entry.450203369_day={self.event.date.day:02d}', url)  # 日
        self.assertIn('entry.1010494053_hour=21', url)  # 開始時間（時）
        self.assertIn('entry.1010494053_minute=00', url)  # 開始時間（分）
        self.assertIn('entry.203043324_hour=22', url)  # 終了時間（時）
        self.assertIn('entry.203043324_minute=00', url)  # 終了時間（分）
        self.assertIn(f'entry.1261006949={quote_plus(PLATFORM_MAP["All"])}', url)  # プラットフォーム
        self.assertIn('entry.2064647146=%E8%AA%B0%E3%81%A7%E3%82%82%E5%8F%82%E5%8A%A0%E5%8F%AF%E8%83%BD', url)  # 参加条件
        self.assertIn('entry.701384676=This+is+a+test+event+for+VRC+Calendar.+Location%3A+VRChat', url)  # イベント詳細
        self.assertIn('entry.1540217995=Test+Organizer', url)  # 主催者
        self.assertIn(
            'entry.1285455202=VRChat%E3%81%A7%E3%80%8CTest+Event%E3%80%8D%E3%82%92%E6%A4%9C%E7%B4%A2%E3%81%97%E3%81%A6%E3%81%8F%E3%81%A0%E3%81%95%E3%81%84%E3%80%82',
            url)  # 参加方法
        self.assertIn('entry.586354013=Tags%3A+test%2C+vrc%2C+calendar.+URL%3A+https%3A%2F%2Fexample.com', url)  # 備考
        self.assertIn('entry.1607289186=No', url)  # 海外ユーザー向け
        self.assertIn('entry.1606730788=%E3%81%9D%E3%81%AE%E4%BB%96%E4%BA%A4%E6%B5%81%E4%BC%9A', url)  # イベントジャンル
        self.assertIn('entry.1606730788=VR%E9%A3%B2%E3%81%BF%E4%BC%9A', url)  # イベントジャンル

        print("Generated URL:", url)
        self.assertTrue(isinstance(url, str))
