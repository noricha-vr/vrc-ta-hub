# app/event_calendar/calendar_utils.py

from urllib.parse import urlencode

from .models import CalendarEntry

FORM_URL = 'https://docs.google.com/forms/d/e/1FAIpQLSevo0ax6ALIzllRCT7up-3KZkohD3VfG28rcOy8XMqDwRWevQ/formResponse'


def create_calendar_entry_url(entry: CalendarEntry) -> str:
    """
    CalendarEntryオブジェクトからGoogleフォームのURLを生成する

    Args:
        entry (CalendarEntry): カレンダーエントリオブジェクト

    Returns:
        str: 生成されたGoogleフォームのURL
    """
    form_data = {
        'entry.1010494053_hour': entry.start_datetime.strftime('%H'),
        'entry.1010494053_minute': entry.start_datetime.strftime('%M'),
        'entry.203043324_hour': entry.end_datetime.strftime('%H'),
        'entry.203043324_minute': entry.end_datetime.strftime('%M'),
        'entry.450203369_year': entry.start_datetime.strftime('%Y'),
        'entry.450203369_month': entry.start_datetime.strftime('%m'),
        'entry.450203369_day': entry.start_datetime.strftime('%d'),
        'entry.426573786': entry.name,
        'entry.1261006949': entry.android_support.value,
        'entry.2064647146': entry.join_condition,
        'entry.701384676': entry.event_detail,
        'entry.1540217995': entry.organizer,
        'entry.1285455202': entry.how_to_join,
        'entry.586354013': entry.note if entry.note else '',
        'entry.1607289186': 'Yes' if entry.is_overseas_user else 'No',
    }

    # イベントジャンルの処理
    event_type_field = 'entry.1606730788'
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

    return f"{FORM_URL}?{'&'.join(url_params)}"
