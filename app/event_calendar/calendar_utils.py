# app/event_calendar/calendar_utils.py

from datetime import datetime, timedelta
from urllib.parse import urlencode, quote, quote_plus
from django.utils import timezone
from django.urls import reverse
from django.core.cache import cache
from django.utils.decorators import method_decorator
from functools import lru_cache
from typing import Dict, Any

from .models import CalendarEntry

FORM_URL = 'https://docs.google.com/forms/d/e/1FAIpQLSfJlabb7niRTf4rX2Q0wRc3ua9MuOEIKveo7NirR6zuOo6D9A/viewform'

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
    'PC': 'PC',
    'All': 'PC/android',
    'Android': 'android only',
}


def create_calendar_entry_url(event: 'Event') -> str:
    """
    EventオブジェクトからGoogleフォームのURLを生成する
    キャッシュ有効時間: 1時間

    Args:
        event (Event): イベントオブジェクト

    Returns:
        str: 生成されたGoogleフォームのURL
    """
    calendar_entry = CalendarEntry.get_or_create_from_event(event)

    cache_key = f'calendar_entry_url_{event.id}_{calendar_entry.is_overseas_user}'
    cached_url = cache.get(cache_key)
    if cached_url:
        return cached_url

    community = event.community
    start_datetime = datetime.combine(event.date, event.start_time)
    end_datetime = start_datetime + timedelta(minutes=event.duration)

    form_data: Dict[str, Any] = {
        'usp': 'pp_url',
        'entry.1319903296': community.name,
        'entry.1310854397_year': start_datetime.strftime('%Y'),
        'entry.1310854397_month': start_datetime.strftime('%m'),
        'entry.1310854397_day': start_datetime.strftime('%d'),
        'entry.1310854397_hour': start_datetime.strftime('%H'),
        'entry.1310854397_minute': start_datetime.strftime('%M'),
        'entry.2042374434_year': end_datetime.strftime('%Y'),
        'entry.2042374434_month': end_datetime.strftime('%m'),
        'entry.2042374434_day': end_datetime.strftime('%d'),
        'entry.2042374434_hour': end_datetime.strftime('%H'),
        'entry.2042374434_minute': end_datetime.strftime('%M'),
        'entry.1704463647': 'イベントを登録する',
        'entry.1354615990': community.organizers,
        'entry.402615171': calendar_entry.event_detail,
        'entry.1470688692': calendar_entry.join_condition,
        'entry.43975396': calendar_entry.how_to_join,
        'entry.131997623': calendar_entry.note if calendar_entry.note else '',
        'entry.1957263813': calendar_entry.x_post_text,
    }

    # 海外ユーザー向け告知は 'dlut' という値で送信されるが、
    # ユーザー設定で削除されたため、ここでは何も設定しない。
    # 必要であれば、以下のコメントを解除し、calendar_entryにis_overseas_userフィールドを追加
    # if calendar_entry.is_overseas_user:
    #     form_data['entry.686419094'] = 'dlut' # 'dlut'はダミー値

    # プラットフォームの処理 (ラジオボタン)
    platform_field = 'entry.412548841'
    platform_value = PLATFORM_MAP.get(community.platform, community.platform)
    if platform_value:
        form_data[f'{platform_field}_sentinel'] = ''
        form_data[platform_field] = platform_value

    # イベントジャンルの処理 (チェックボックス)
    event_type_field = 'entry.1923252134'
    if calendar_entry.event_genres:
        form_data[f'{event_type_field}_sentinel'] = ''
        form_data[event_type_field] = []
        for genre in calendar_entry.event_genres:
            mapped_genre = EVENT_GENRE_MAP.get(genre, genre)
            form_data[event_type_field].append(mapped_genre)

    # URLエンコーディング（複数値のパラメータに対応）
    # Googleフォームのviewformエンドポイントではurlencodeを使用
    params = []
    for key, value in form_data.items():
        if isinstance(value, list):
            # リストの場合、各値を個別のパラメータとして追加
            for v in value:
                params.append((key, str(v)))
        else:
            params.append((key, str(value)))
    
    # urlencodeを使用してパラメータをエンコード
    url_with_params = f"{FORM_URL}?{urlencode(params)}"
    
    # ページ履歴を追加して複数ページのフォームに対応
    url_with_params += "&pageHistory=0,1,2"
    
    # キャッシュに保存（1時間）
    cache.set(cache_key, url_with_params, 60 * 60)
    
    return url_with_params


@lru_cache(maxsize=128)
def generate_google_calendar_url(request, event):
    """
    Googleカレンダーにイベントを追加するためのURLを生成する
    キャッシュ有効時間: 1時間
    
    Args:
        request: HTTPリクエストオブジェクト
        event: イベントオブジェクト

    Returns:
        str: GoogleカレンダーのイベントURL
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
    cache.set(cache_key, url, 60 * 60)
    
    return url
