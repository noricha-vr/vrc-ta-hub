#!/usr/bin/env python
"""マスターイベントの日付設計を分析"""

import os
import sys
import django
from datetime import datetime

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from event.models import Event, RecurrenceRule
from community.models import Community


def analyze_master_dates():
    """マスターイベントの日付設計を分析"""
    print('=== マスターイベント日付設計の分析 ===\n')
    
    # マスターイベントを取得（日付順）
    master_events = Event.objects.filter(
        is_recurring_master=True
    ).select_related('community', 'recurrence_rule').order_by('date')
    
    today = datetime.now().date()
    
    # 統計情報
    past_masters = 0
    current_masters = 0
    future_masters = 0
    
    print('マスターイベントの日付分析:')
    print('-' * 80)
    
    for master in master_events[:10]:  # 最初の10件を詳細表示
        # 日付の状態を判定
        if master.date < today:
            status = '過去'
            past_masters += 1
        elif master.date == today:
            status = '今日'
            current_masters += 1
        else:
            status = '未来'
            future_masters += 1
        
        # インスタンスの情報
        instances = Event.objects.filter(recurring_master=master)
        instance_count = instances.count()
        
        if instance_count > 0:
            first_instance = instances.order_by('date').first()
            days_diff = (first_instance.date - master.date).days
        else:
            first_instance = None
            days_diff = None
        
        print(f'{master.community.name[:20]:20} | {master.date} ({status}) | '
              f'インスタンス: {instance_count:3}件 | '
              f'初回までの差: {f"{days_diff:3}日" if days_diff is not None else "N/A"}')
    
    # 全体の統計
    total_masters = master_events.count()
    for master in master_events[10:]:  # 残りも集計
        if master.date < today:
            past_masters += 1
        elif master.date == today:
            current_masters += 1
        else:
            future_masters += 1
    
    print(f'\n統計サマリー:')
    print(f'  総マスターイベント数: {total_masters}')
    print(f'  - 過去の日付: {past_masters} ({past_masters/total_masters*100:.1f}%)')
    print(f'  - 今日の日付: {current_masters} ({current_masters/total_masters*100:.1f}%)')
    print(f'  - 未来の日付: {future_masters} ({future_masters/total_masters*100:.1f}%)')
    
    # マスターイベントの日付とRecurrenceRuleの開始日の関係を分析
    print(f'\n\nマスターイベント日付とRecurrenceRule開始日の関係:')
    print('-' * 80)
    
    for master in master_events[:10]:
        if hasattr(master, 'recurrence_rule') and master.recurrence_rule:
            rule = master.recurrence_rule
            if rule.start_date:
                diff = (rule.start_date - master.date).days
                print(f'{master.community.name[:20]:20} | '
                      f'マスター: {master.date} | '
                      f'ルール開始: {rule.start_date} | '
                      f'差: {diff:4}日')


if __name__ == '__main__':
    analyze_master_dates()