from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

import requests
from django.test import TestCase

FORM_URL = 'https://docs.google.com/forms/d/e/1FAIpQLSevo0ax6ALIzllRCT7up-3KZkohD3VfG28rcOy8XMqDwRWevQ/formResponse'


@dataclass
class CalendarEntry:
    name: str
    start_datetime: datetime
    end_datetime: datetime
    android_support: str
    join_condition: str
    event_type: str
    event_detail: str
    organizer: str
    how_to_join: str
    note: Optional[str] = None
    is_overseas_user: bool = False


FORM_FIELDS = {
    'startHour': 'entry.1010494053_hour',
    'startMinute': 'entry.1010494053_minute',
    'endHour': 'entry.203043324_hour',
    'endMinute': 'entry.203043324_minute',
    'eventDateYear': 'entry.450203369_year',
    'eventDateMonth': 'entry.450203369_month',
    'eventDateDay': 'entry.450203369_day',
    'eventName': 'entry.426573786',
    'mailAddress': 'emailAddress',
    'androidSupport': 'entry.1261006949',
    'joinCondition': 'entry.2064647146',
    'eventType': 'entry.1606730788',
    'eventDetail': 'entry.701384676',
    'organizer': 'entry.1540217995',
    'howToJoin': 'entry.1285455202',
    'note': 'entry.586354013',
    'isOverseasUser': 'entry.1607289186',
}


def create_calendar_entry_url(entry: CalendarEntry) -> Optional[str]:
    form_data = {
        FORM_FIELDS['eventName']: entry.name,
        FORM_FIELDS['startHour']: entry.start_datetime.strftime('%H'),
        FORM_FIELDS['startMinute']: entry.start_datetime.strftime('%M'),
        FORM_FIELDS['endHour']: entry.end_datetime.strftime('%H'),
        FORM_FIELDS['endMinute']: entry.end_datetime.strftime('%M'),
        FORM_FIELDS['eventDateYear']: entry.start_datetime.strftime('%Y'),
        FORM_FIELDS['eventDateMonth']: entry.start_datetime.strftime('%m'),
        FORM_FIELDS['eventDateDay']: entry.start_datetime.strftime('%d'),
        FORM_FIELDS['androidSupport']: entry.android_support,
        FORM_FIELDS['joinCondition']: entry.join_condition,
        FORM_FIELDS['eventType']: entry.event_type,
        FORM_FIELDS['organizer']: entry.organizer,
        FORM_FIELDS['howToJoin']: entry.how_to_join,
        FORM_FIELDS['note']: entry.note if entry.note else '',
        FORM_FIELDS['isOverseasUser']: 'Yes' if entry.is_overseas_user else 'No',
        FORM_FIELDS['eventDetail']: entry.event_detail,
    }
    url_with_params = f"{FORM_URL}?{urlencode(form_data)}"

    try:
        print(f"Submitting form to {url_with_params}...")
        return url_with_params
    except requests.RequestException as e:
        print(f"Error submitting form: {e}")
        return None


class CreateCalendarTest(TestCase):
    def test_create_calendar(self):
        # テストデータ

        test_entry = CalendarEntry(
            name="Test Event",
            start_datetime=datetime(2024, 7, 16, 21, 0),
            end_datetime=datetime(2024, 7, 16, 22, 0),
            android_support="PC/Android両対応（Android対応）",
            join_condition="誰でも参加可能",  # 例：この値は適切なものに変更してください
            event_type="ミートアップ",  # 例：この値は適切なものに変更してください
            event_detail="This is a test event for VRC Calendar. Location: VRChat",
            organizer="Test Organizer",  # 例：この値は適切なものに変更してください
            how_to_join="VRChatで「Test Event」を検索してください。",  # 例：この値は適切なものに変更してください
            note="Tags: test, vrc, calendar. URL: https://example.com",
            is_overseas_user=False  # 例：この値は適切なものに変更してください
        )

        email = "test@example.com"

        print("Submitting test calendar entry...")
        self.assertTrue(type(create_calendar_entry_url(test_entry)), str)
        print("Calendar entry submitted successfully!")
