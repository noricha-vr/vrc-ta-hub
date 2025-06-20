#!/usr/bin/env python
"""曜日が間違っているイベントを分析"""

import os
import sys
import django
from datetime import datetime

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from event.models import Event
from django.utils import timezone


def analyze_wrong_weekday_events():
    """曜日が間違っているイベントを分析"""
    
    today = timezone.now().date()
    print(f"今日の日付: {today} ({today.strftime('%A')})")
    print("="*50)
    
    # 問題のあるイベントを特定
    wrong_weekday_events = []
    
    events = Event.objects.filter(date=today).select_related('community', 'recurring_master__recurrence_rule')
    
    for event in events:
        if event.recurring_master and event.recurring_master.recurrence_rule:
            rule = event.recurring_master.recurrence_rule
            
            if rule.start_date:
                expected_weekday = rule.start_date.weekday()
                actual_weekday = event.date.weekday()
                
                if expected_weekday != actual_weekday:
                    wrong_weekday_events.append({
                        'event': event,
                        'rule': rule,
                        'expected': rule.start_date.strftime('%A'),
                        'actual': event.date.strftime('%A'),
                        'expected_date': rule.start_date
                    })
    
    print(f"\n曜日が間違っているイベント: {len(wrong_weekday_events)}件\n")
    
    # 頻度別に分類
    weekly_wrong = []
    monthly_wrong = []
    other_wrong = []
    
    for item in wrong_weekday_events:
        rule = item['rule']
        if rule.frequency == 'WEEKLY':
            weekly_wrong.append(item)
        elif rule.frequency == 'MONTHLY_BY_WEEK':
            monthly_wrong.append(item)
        else:
            other_wrong.append(item)
    
    # 週次イベントの問題
    if weekly_wrong:
        print("【週次イベントの問題】")
        for item in weekly_wrong:
            event = item['event']
            print(f"\n{event.community.name}")
            print(f"  期待: {item['expected']} (start_date: {item['expected_date']})")
            print(f"  実際: {item['actual']}")
            print(f"  修正が必要です")
    
    # 月次イベントの問題
    if monthly_wrong:
        print("\n【月次イベントの問題】")
        for item in monthly_wrong:
            event = item['event']
            rule = item['rule']
            print(f"\n{event.community.name}")
            print(f"  ルール: 第{rule.week_of_month}{item['expected']}")
            print(f"  期待: {item['expected']} (start_date: {item['expected_date']})")
            print(f"  実際: {item['actual']}")
            
            # 今月の正しい日付を計算
            from dateutil.relativedelta import relativedelta
            correct_date = find_nth_weekday_of_month(
                today.year, 
                today.month, 
                rule.week_of_month, 
                expected_weekday
            )
            if correct_date:
                print(f"  今月の正しい日付: {correct_date}")
    
    # 削除すべきイベントのリスト
    print("\n【削除すべきイベント】")
    delete_count = 0
    for item in wrong_weekday_events:
        event = item['event']
        print(f"- {event.community.name} (ID: {event.id})")
        delete_count += 1
    
    print(f"\n合計 {delete_count}件のイベントを削除する必要があります")
    
    return wrong_weekday_events


def find_nth_weekday_of_month(year, month, nth, weekday):
    """指定された月の第N週の特定曜日を見つける"""
    from datetime import date, timedelta
    
    # 月の最初の日
    first_day = date(year, month, 1)
    
    # 最初の指定曜日を見つける
    days_until_weekday = (weekday - first_day.weekday()) % 7
    first_occurrence = first_day + timedelta(days=days_until_weekday)
    
    # 第N週の日付を計算
    target_date = first_occurrence + timedelta(weeks=(nth - 1))
    
    # 月をまたいでいないか確認
    if target_date.month == month:
        return target_date
    else:
        return None


if __name__ == '__main__':
    analyze_wrong_weekday_events()