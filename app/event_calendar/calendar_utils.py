# app/event_calendar/calendar_utils.py

from datetime import datetime, timedelta
from urllib.parse import urlencode

from .models import CalendarEntry

FORM_URL = 'https://docs.google.com/forms/d/e/1FAIpQLSevo0ax6ALIzllRCT7up-3KZkohD3VfG28rcOy8XMqDwRWevQ/formResponse'

EVENT_GENRE_MAP = {
    'OTHER_MEETUP': 'その他交流会',
    'VR_DRINKING_PARTY': 'VR飲み会',
    'AVATAR_FITTING': 'アバター試着会',
    'MODIFIED_AVATAR_MEETUP': '改変アバター交流会',
    'STORE_EVENT': '店舗系イベント',
    'MUSIC_EVENT': '音楽系イベント',
    'ACADEMIC_EVENT': '学術系イベント',
    'ROLEPLAY': 'ロールプレイ',
    'BEGINNER_EVENT': '初心者向けイベント',
    'REGULAR_EVENT': '定期イベント',
}

PLATFORM_MAP = {
    'PC': 'PCオンリー',
    'All': 'PC/Android両対応（Android対応）',
    'Android': 'Android オンリー',
}


def create_calendar_entry_url(event: 'Event') -> str:
    """
    EventオブジェクトからGoogleフォームのURLを生成する

    Args:
        event (Event): イベントオブジェクト

    Returns:
        str: 生成されたGoogleフォームのURL
    """
    calendar_entry = CalendarEntry.get_or_create_from_event(event)
    community = event.community
    start_datetime = datetime.combine(event.date, event.start_time)
    end_datetime = start_datetime + timedelta(minutes=event.duration)

    form_data: Dict[str, Any] = {
        'entry.426573786': community.name,
        'entry.1010494053_hour': start_datetime.strftime('%H'),
        'entry.1010494053_minute': start_datetime.strftime('%M'),
        'entry.203043324_hour': end_datetime.strftime('%H'),
        'entry.203043324_minute': end_datetime.strftime('%M'),
        'entry.450203369_year': start_datetime.strftime('%Y'),
        'entry.450203369_month': start_datetime.strftime('%m'),
        'entry.450203369_day': start_datetime.strftime('%d'),
        'entry.1261006949': PLATFORM_MAP.get(community.platform, community.platform),
        'entry.2064647146': calendar_entry.join_condition,
        'entry.1540217995': community.organizers,
        'entry.1285455202': calendar_entry.how_to_join,
        'entry.586354013': calendar_entry.note if calendar_entry.note else '',
        'entry.1607289186': 'Yes' if calendar_entry.is_overseas_user else 'No',
        'entry.701384676': calendar_entry.event_detail,
    }

    # イベントジャンルの処理
    event_type_field = 'entry.1606730788'
    for genre in calendar_entry.event_genres:
        mapped_genre = EVENT_GENRE_MAP.get(genre, genre)
        if event_type_field in form_data:
            form_data[event_type_field].append(mapped_genre)
        else:
            form_data[event_type_field] = [mapped_genre]

    # URLエンコーディング（複数値のパラメータに対応）
    url_params = []
    for key, value in form_data.items():
        if isinstance(value, list):
            for v in value:
                url_params.append(f"{key}={urlencode({key: v})[len(key) + 1:]}")
        else:
            url_params.append(f"{key}={urlencode({key: value})[len(key) + 1:]}")

    url_with_params = f"{FORM_URL}?{'&'.join(url_params)}"

    return url_with_params
