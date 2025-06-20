#!/usr/bin/env python
import os
import sys
import django

# Djangoの設定
sys.path.insert(0, '/opt/project/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ta_hub.settings')
django.setup()

from event_calendar.libs import get_events_from_google_calendar
from datetime import datetime, timedelta
import pytz

# Googleカレンダーから過去のイベントを取得
jst = pytz.timezone('Asia/Tokyo')
time_min = datetime(2024, 1, 1, tzinfo=jst)
time_max = datetime(2025, 12, 31, tzinfo=jst)

print('Googleカレンダーからイベント情報を取得中...')
try:
    events = get_events_from_google_calendar(time_min=time_min, time_max=time_max)
    
    # VR研究Cafeのイベントをフィルタ
    vr_cafe_events = [e for e in events if 'VR研究Cafe' in e.get('summary', '')]
    
    print(f'\nVR研究Cafeのイベント数: {len(vr_cafe_events)}件')
    
    # 過去のイベントを日付順に表示
    print('\n過去のイベント（古い順）:')
    sorted_events = sorted(vr_cafe_events, key=lambda x: x.get('start', {}).get('dateTime', ''))
    
    # 10日、20日、30日の開催パターンを確認
    day_counts = {}
    weekday_counts = {}
    
    for event in sorted_events:
        start = event.get('start', {})
        if 'dateTime' in start:
            start_dt = datetime.fromisoformat(start['dateTime'].replace('Z', '+00:00'))
            start_dt = start_dt.astimezone(jst)
            weekday_map = {0: '月', 1: '火', 2: '水', 3: '木', 4: '金', 5: '土', 6: '日'}
            weekday = weekday_map[start_dt.weekday()]
            
            # 最初の20件を表示
            if sorted_events.index(event) < 20:
                print(f'  {start_dt.strftime("%Y-%m-%d")} ({weekday}) {start_dt.strftime("%H:%M")} - {event.get("summary", "No title")}')
            
            # 統計情報を集計
            day = start_dt.day
            if day not in day_counts:
                day_counts[day] = 0
            day_counts[day] += 1
            
            if weekday not in weekday_counts:
                weekday_counts[weekday] = 0
            weekday_counts[weekday] += 1
    
    print('\n開催日の集計:')
    for day in sorted(day_counts.keys()):
        print(f'  {day}日: {day_counts[day]}回')
    
    print('\n曜日の集計:')
    for weekday in ['月', '火', '水', '木', '金', '土', '日']:
        if weekday in weekday_counts:
            print(f'  {weekday}曜日: {weekday_counts[weekday]}回')
            
except Exception as e:
    print(f'エラー: {e}')
    import traceback
    traceback.print_exc()