from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional

from django.test import TestCase

FORM_URL = 'https://docs.google.com/forms/d/e/1FAIpQLSevo0ax6ALIzllRCT7up-3KZkohD3VfG28rcOy8XMqDwRWevQ/formResponse'


class AndroidSupport(Enum):
    PC_ONLY = "PCオンリー"
    PC_ANDROID = "PC/Android両対応（Android対応）"
    ANDROID_ONLY = "Android オンリー"


class EventGenre(Enum):
    AVATAR_FITTING = "アバター試着会"
    MODIFIED_AVATAR_MEETUP = "改変アバター交流会"
    OTHER_MEETUP = "その他交流会"
    VR_DRINKING_PARTY = "VR飲み会"
    STORE_EVENT = "店舗系イベント"
    MUSIC_EVENT = "音楽系イベント"
    ACADEMIC_EVENT = "学術系イベント"
    ROLEPLAY = "ロールプレイ"
    BEGINNER_EVENT = "初心者向けイベント"
    REGULAR_EVENT = "定期イベント"


@dataclass
class CalendarEntry:
    name: str
    start_datetime: datetime
    end_datetime: datetime
    android_support: AndroidSupport
    join_condition: str
    event_detail: str
    organizer: str
    how_to_join: str
    note: Optional[str] = None
    is_overseas_user: bool = False
    event_genres: List[EventGenre] = field(default_factory=list)


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

from urllib.parse import urlencode


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
        FORM_FIELDS['androidSupport']: entry.android_support.value,
        FORM_FIELDS['joinCondition']: entry.join_condition,
        FORM_FIELDS['organizer']: entry.organizer,
        FORM_FIELDS['howToJoin']: entry.how_to_join,
        FORM_FIELDS['note']: entry.note if entry.note else '',
        FORM_FIELDS['isOverseasUser']: 'Yes' if entry.is_overseas_user else 'No',
        FORM_FIELDS['eventDetail']: entry.event_detail,
    }

    # 複数選択チェックボックスのための処理
    event_type_field = FORM_FIELDS['eventType']
    for genre in entry.event_genres:
        if event_type_field in form_data:
            form_data[event_type_field].append(genre.value)
        else:
            form_data[event_type_field] = [genre.value]

    # URLエンコーディング（複数値のパラメータに対応）
    url_params = []
    for key, value in form_data.items():
        if isinstance(value, list):
            for v in value:
                url_params.append(f"{key}={urlencode({key: v})[len(key) + 1:]}")
        else:
            url_params.append(f"{key}={urlencode({key: value})[len(key) + 1:]}")

    url_with_params = f"{FORM_URL}?{'&'.join(url_params)}"

    print(f"Submitting form to {url_with_params}")
    return url_with_params


class CreateCalendarTest(TestCase):
    def test_create_calendar(self):
        test_entry = CalendarEntry(
            name="Test Event",
            start_datetime=datetime(2024, 7, 16, 21, 0),
            end_datetime=datetime(2024, 7, 16, 22, 0),
            android_support=AndroidSupport.PC_ANDROID,
            join_condition="誰でも参加可能",
            event_genres=[EventGenre.OTHER_MEETUP, EventGenre.VR_DRINKING_PARTY],
            event_detail="This is a test event for VRC Calendar. Location: VRChat",
            organizer="Test Organizer",
            how_to_join="VRChatで「Test Event」を検索してください。",
            note="Tags: test, vrc, calendar. URL: https://example.com",
            is_overseas_user=False
        )

        print("Submitting test calendar entry...")
        self.assertTrue(type(create_calendar_entry_url(test_entry)), str)
        print("Calendar entry submitted successfully!")
