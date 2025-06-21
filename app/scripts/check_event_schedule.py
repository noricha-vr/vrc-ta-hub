#!/usr/bin/env python
"""30日間のイベントスケジュールを確認するスクリプト"""
import os
import sys
import django
from datetime import timedelta

sys.path.append('/app/website')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from event.models import Event
from django.utils import timezone

# 特定の日付範囲でイベントを確認
today = timezone.now().date()
end_date = today + timedelta(days=30)

print(f'今日から30日間のイベントを確認 ({today} - {end_date})')
print('-' * 60)

events = Event.objects.filter(
    date__gte=today,
    date__lte=end_date
).order_by('date', 'start_time', 'community__name')

current_date = None
for event in events:
    if event.date != current_date:
        current_date = event.date
        print(f'\n{current_date} ({current_date.strftime("%A")}):')
    
    master_info = ''
    if event.is_recurring_master:
        master_info = ' [MASTER]'
    elif event.recurring_master:
        master_info = f' [Instance of #{event.recurring_master_id}]'
    
    print(f'  {event.start_time} - {event.community.name}{master_info}')

print(f'\n合計: {events.count()}件')

# 重複チェック
from collections import Counter
event_keys = [(e.community.name, e.date, e.start_time) for e in events]
duplicates = [k for k, v in Counter(event_keys).items() if v > 1]
print(f'\n重複イベント: {len(duplicates)}件')
if duplicates:
    for dup in duplicates:
        print(f'  - {dup[0]} on {dup[1]} at {dup[2]}')