import datetime
from urllib.parse import quote_plus

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from account.models import CustomUser
from community.models import Community
from event.models import Event
from event_calendar.calendar_utils import create_calendar_entry_url, PLATFORM_MAP
from event_calendar.models import CalendarEntry


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
        self.end_date = self.event.date
        if (self.event.start_time.hour * 60 + self.event.start_time.minute + self.event.duration) >= 24 * 60:
            self.end_date += datetime.timedelta(days=1)

    def test_create_calendar_entry_url(self):
        calendar_entry = CalendarEntry.get_or_create_from_event(self.event)
        calendar_entry.join_condition = "誰でも参加可能"
        calendar_entry.event_detail = "This is a test event for VRC Calendar. Location: VRChat"
        calendar_entry.how_to_join = "VRChatで「Test Event」を検索してください。"
        calendar_entry.note = "Tags: test, vrc, calendar. URL: https://example.com"
        calendar_entry.is_overseas_user = False
        calendar_entry.event_genres = ["OTHER_MEETUP", "VR_DRINKING_PARTY"]
        calendar_entry.x_post_text = "This is a test post for X."
        calendar_entry.save()

        # キャッシュをクリア
        cache.clear()
        url = create_calendar_entry_url(self.event)

        # URLの基本構造を確認
        self.assertTrue(url.startswith(
            'https://docs.google.com/forms/d/e/1FAIpQLSfJlabb7niRTf4rX2Q0wRc3ua9MuOEIKveo7NirR6zuOo6D9A/viewform?'))
        self.assertIn('usp=pp_url', url)

        # 重要なパラメータの存在を確認
        self.assertIn('entry.1319903296=Test+Community', url)
        self.assertIn(f'entry.1310854397_year={self.event.date.year}', url)
        self.assertIn(f'entry.1310854397_month={self.event.date.month:02d}', url)
        self.assertIn(f'entry.1310854397_day={self.event.date.day:02d}', url)
        self.assertIn('entry.1310854397_hour=21', url)
        self.assertIn('entry.1310854397_minute=00', url)
        self.assertIn(f'entry.2042374434_year={self.end_date.year}', url)
        self.assertIn(f'entry.2042374434_month={self.end_date.month:02d}', url)
        self.assertIn(f'entry.2042374434_day={self.end_date.day:02d}', url)
        self.assertIn('entry.2042374434_hour=22', url)
        self.assertIn('entry.2042374434_minute=00', url)

        # プラットフォームのテスト
        self.assertIn('entry.412548841_sentinel=', url)
        self.assertIn(f'entry.412548841={quote_plus(PLATFORM_MAP["All"])}', url)

        # 新しいフォームのフィールドをテスト
        self.assertIn(f'entry.1470688692={quote_plus("誰でも参加可能")}', url)
        self.assertIn(f'entry.402615171={quote_plus("This is a test event for VRC Calendar. Location: VRChat")}', url)
        self.assertIn(f'entry.1354615990={quote_plus("Test Organizer")}', url)
        self.assertIn(f'entry.43975396={quote_plus("VRChatで「Test Event」を検索してください。")}', url)
        self.assertIn(f'entry.131997623={quote_plus("Tags: test, vrc, calendar. URL: https://example.com")}', url)
        self.assertIn(f'entry.1957263813={quote_plus("This is a test post for X.")}', url)

        # イベントジャンルのテスト
        self.assertIn('entry.1923252134_sentinel=', url)
        self.assertIn(f'entry.1923252134={quote_plus("その他交流会")}', url)
        self.assertIn(f'entry.1923252134={quote_plus("VR飲み会")}', url)

        self.assertNotIn('entry.686419094=', url)
        self.assertIn(f'entry.1704463647={quote_plus("イベントを登録する")}', url)
        self.assertIn('&pageHistory=0,1,2', url)

        print("Generated URL (non-overseas):", url)
        self.assertTrue(isinstance(url, str))

    def test_get_or_create_from_event(self):
        # 最初の呼び出しで新しいCalendarEntryが作成されることを確認
        calendar_entry = CalendarEntry.get_or_create_from_event(self.event)
        self.assertEqual(calendar_entry.community, self.community)
        self.assertEqual(CalendarEntry.objects.count(), 1)

        # 2回目の呼び出しで同じCalendarEntryが返されることを確認
        same_calendar_entry = CalendarEntry.get_or_create_from_event(self.event)
        self.assertEqual(calendar_entry, same_calendar_entry)
        self.assertEqual(CalendarEntry.objects.count(), 1)

    def test_event_genres_validation(self):
        # 有効なジャンルでCalendarEntryが作成できることを確認
        valid_entry = CalendarEntry(
            community=self.community,
            event_genres=["AVATAR_FITTING", "VR_DRINKING_PARTY"]
        )
        valid_entry.full_clean()  # ValidationErrorが発生しないことを確認

        # 無効なジャンルでValidationErrorが発生することを確認
        invalid_entry = CalendarEntry(
            community=self.community,
            event_genres=["INVALID_GENRE"]
        )
        with self.assertRaises(ValidationError):
            invalid_entry.full_clean()

    def test_get_event_genres_display(self):
        calendar_entry = CalendarEntry.objects.create(
            community=self.community,
            event_genres=["AVATAR_FITTING", "VR_DRINKING_PARTY"]
        )
        display_genres = calendar_entry.get_event_genres_display()
        self.assertEqual(display_genres, ["アバター試着会", "VR飲み会"])

    def test_overseas_user_setting(self):
        calendar_entry = CalendarEntry.get_or_create_from_event(self.event)
        calendar_entry.join_condition = "誰でも参加可能"
        calendar_entry.event_detail = "This is a test event for overseas users"
        calendar_entry.how_to_join = "Search for Test Event in VRChat"
        calendar_entry.note = "International event"
        calendar_entry.is_overseas_user = True  # 海外ユーザー向けを有効化
        calendar_entry.event_genres = ["REGULAR_EVENT"]
        calendar_entry.x_post_text = "International event announcement"
        calendar_entry.save()

        # キャッシュをクリア
        cache.clear()
        url = create_calendar_entry_url(self.event)

        # 海外ユーザー向けパラメータが含まれていることを確認
        self.assertIn('entry.686419094=dlut', url)

        print("Generated URL (overseas):", url)

        # 別のイベントで海外ユーザー向けが無効の場合
        calendar_entry.is_overseas_user = False
        calendar_entry.save()

        # キャッシュをクリア
        cache.clear()
        url_non_overseas = create_calendar_entry_url(self.event)

        # 海外ユーザー向けパラメータが含まれていないことを確認
        self.assertNotIn('entry.686419094=', url_non_overseas)
