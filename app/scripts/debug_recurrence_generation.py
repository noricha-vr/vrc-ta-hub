#!/usr/bin/env python
"""定期イベント生成のデバッグ"""

import os
import sys
import django
from datetime import datetime, timedelta

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.utils import timezone
from event.models import Event, RecurrenceRule
from event.recurrence_service import RecurrenceService
from community.models import Community


def debug_generation():
    """定期イベント生成プロセスをデバッグ"""
    print('=== 定期イベント生成デバッグ ===\n')
    
    # VR酔い訓練集会を例に詳細を確認
    community = Community.objects.get(name='VR酔い訓練集会')
    master = Event.objects.get(community=community, is_recurring_master=True)
    rule = master.recurrence_rule
    service = RecurrenceService()
    
    print(f'コミュニティ: {community.name}')
    print(f'マスターイベント:')
    print(f'  - ID: {master.id}')
    print(f'  - 日付: {master.date}')
    print(f'  - 時刻: {master.start_time}')
    print(f'  - is_recurring_master: {master.is_recurring_master}')
    print(f'\n定期ルール:')
    print(f'  - 頻度: {rule.frequency}')
    print(f'  - 開始日: {rule.start_date}')
    print(f'  - 間隔: {rule.interval}')
    
    # 最後のインスタンスを確認
    last_instance = Event.objects.filter(
        recurring_master=master
    ).order_by('-date').first()
    
    print(f'\n最後のインスタンス:')
    if last_instance:
        print(f'  - 日付: {last_instance.date}')
        base_date = last_instance.date + timedelta(days=1)
        print(f'  - 基準日（最後のインスタンス + 1日）: {base_date}')
    else:
        base_date = master.date
        print(f'  - なし（基準日はマスターイベントの日付: {base_date}）')
    
    # 今日より前の日付なら今日に設定
    today = timezone.now().date()
    if base_date < today:
        print(f'  - 基準日が過去のため今日に更新: {base_date} → {today}')
        base_date = today
    
    # 日付生成をシミュレート
    print(f'\n日付生成（1ヶ月先まで）:')
    dates = service.generate_dates(
        rule=rule,
        base_date=base_date,
        base_time=master.start_time,
        months=1,
        community=community
    )
    
    print(f'  生成された日付: {len(dates)}件')
    for i, date in enumerate(dates[:5]):  # 最初の5件を表示
        print(f'    {i+1}. {date}')
    
    # 既存イベントとの重複チェック
    print(f'\n既存イベントとの重複チェック:')
    for date in dates[:3]:  # 最初の3件をチェック
        exists = Event.objects.filter(
            community=community,
            date=date,
            start_time=master.start_time
        ).exists()
        print(f'  - {date}: {"存在する（スキップ）" if exists else "存在しない（作成可能）"}')
    
    # マスターイベントと同じ日付のチェック
    print(f'\nマスターイベントと同じ日付 ({master.date}) のイベント:')
    same_date_events = Event.objects.filter(
        community=community,
        date=master.date
    )
    for event in same_date_events:
        print(f'  - ID: {event.id}, is_recurring_master: {event.is_recurring_master}, recurring_master_id: {event.recurring_master_id}')


if __name__ == '__main__':
    debug_generation()