# app/event_calendar/calendar_utils.py

from datetime import datetime, timedelta
from urllib.parse import urlencode, quote
from django.utils import timezone
from django.urls import reverse
from django.core.cache import cache
from django.utils.decorators import method_decorator
from functools import lru_cache
from typing import Dict, Any

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


@lru_cache(maxsize=128)
def create_calendar_entry_url(event: 'Event') -> str:
    """
    EventオブジェクトからGoogleフォームのURLを生成する
    キャッシュ有効時間: 1時間

    Args:
        event (Event): イベントオブジェクト

    Returns:
        str: 生成されたGoogleフォームのURL
    """
    cache_key = f'calendar_entry_url_{event.id}'
    cached_url = cache.get(cache_key)
    if cached_url:
        return cached_url

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
        'entry.701384676': calendar_entry.event_detail,
    }

    # 海外ユーザー向け告知の処理 (修正後)
    if calendar_entry.is_overseas_user:
        form_data['entry.1607289186'] = '希望する' # チェックボックスの値に合わせる

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
            # リストの場合、各値を個別のパラメータとして追加
            for v in value:
                # quoteを使用して値を適切にエンコード
                url_params.append(f"{key}={quote(str(v))}")
        else:
            # quoteを使用して値を適切にエンコード
            url_params.append(f"{key}={quote(str(value))}")

    url_with_params = f"{FORM_URL}?{'&'.join(url_params)}"
    
    # キャッシュに保存（1時間）
    cache.set(cache_key, url_with_params, 60 * 60)
    
    return url_with_params


@lru_cache(maxsize=128)
def generate_google_calendar_url(request, event):
    """
    Googleカレンダーにイベントを追加するためのURLを生成する
    キャッシュ有効時間: 1時間
    """
    cache_key = f'google_calendar_url_{event.id}'
    cached_url = cache.get(cache_key)
    if cached_url:
        return cached_url

    # イベントの開始と終了の日時を設定
    start_datetime = datetime.combine(event.date, event.start_time)
    end_datetime = start_datetime + timedelta(minutes=event.duration)
    
    # タイムゾーンを設定
    start_datetime = timezone.localtime(timezone.make_aware(start_datetime))
    end_datetime = timezone.localtime(timezone.make_aware(end_datetime))
    
    # コミュニティページのURLを生成
    community_url = request.build_absolute_uri(
        reverse('community:detail', kwargs={'pk': event.community.pk})
    )
    
    # 説明文を作成
    description = [f"参加方法: {community_url}"]
    
    # 発表情報を追加（存在する場合）
    if event.details.exists():
        description.extend([f"発表者: {detail.speaker}\nテーマ: {detail.theme}" for detail in event.details.all()])
    
    # URLパラメータを作成
    params = {
        'action': 'TEMPLATE',
        'text': f"{event.community.name}",  # イベントのタイトル
        'dates': f"{start_datetime.strftime('%Y%m%dT%H%M%S')}/{end_datetime.strftime('%Y%m%dT%H%M%S')}",
        'ctz': 'Asia/Tokyo',  # タイムゾーン
        'details': "\n\n".join(description)  # 説明文
    }
    
    # URLを構築
    base_url = "https://www.google.com/calendar/render?"
    param_strings = [f"{k}={quote(str(v))}" for k, v in params.items()]
    
    url = base_url + "&".join(param_strings)
    
    # キャッシュに保存（1時間）
    cache.set(cache_key, url, 0)
    
    return url
