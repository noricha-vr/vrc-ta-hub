#!/usr/bin/env python
"""各集会のweekdays設定と現在の設定の整合性を確認"""

import os
import sys
import django
from datetime import datetime

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from community.models import Community
from event.models import Event, RecurrenceRule


def check_weekdays_consistency():
    """weekdays設定と現在の設定の整合性を確認"""
    
    # 対象の集会
    communities = [
        'VRC Blender集会',
        'VRC MED J SALON',
        'VR研究Cafe',
        '分散システム集会',
        'VRC微分音集会',
        'アバター改変なんもわからん集会',
        'エンジニア作業飲み集会',
        'Blender＆Unity技術交流会',
        'VR酔い訓練集会'
    ]
    
    print('各集会のweekdays設定（これが正しい情報）と現在の設定の比較')
    print('='*70)
    
    weekday_map = {
        'Mon': 0, 'Tue': 1, 'Wed': 2, 'Thu': 3,
        'Fri': 4, 'Sat': 5, 'Sun': 6
    }
    
    for name in communities:
        try:
            community = Community.objects.get(name=name)
            print(f'\n{name} (ID: {community.id})')
            print(f'  weekdays設定: {community.weekdays}')
            print(f'  frequency: {community.frequency}')
            print(f'  start_time: {community.start_time}')
            
            # RecurrenceRuleを確認
            masters = Event.objects.filter(
                community=community,
                is_recurring_master=True
            ).select_related('recurrence_rule')
            
            for master in masters:
                if master.recurrence_rule:
                    rule = master.recurrence_rule
                    print(f'\n  RecurrenceRule (ID: {rule.id}):')
                    print(f'    frequency: {rule.frequency}')
                    print(f'    start_date: {rule.start_date}')
                    
                    if rule.start_date:
                        print(f'    start_dateの曜日: {rule.start_date.strftime("%A")}')
                    
                    # 曜日の整合性チェック
                    if rule.start_date and community.weekdays and community.weekdays[0] != 'Other':
                        expected_weekday = weekday_map.get(community.weekdays[0])
                        actual_weekday = rule.start_date.weekday()
                        
                        if expected_weekday != actual_weekday:
                            print(f'    ⚠️ 曜日不一致！')
                            print(f'       期待される曜日: {community.weekdays[0]}')
                            print(f'       実際のstart_date: {rule.start_date.strftime("%A")}')
                            print(f'    → start_dateの修正が必要')
                        else:
                            print(f'    ✓ 曜日一致')
                    
                    # 今日のイベントを確認
                    today = datetime.now().date()
                    today_event = Event.objects.filter(
                        community=community,
                        date=today
                    ).first()
                    
                    if today_event:
                        print(f'\n  今日のイベント:')
                        print(f'    存在する（{today_event.date} {today_event.start_time}）')
                        
                        # 今日がweekdaysの曜日かチェック
                        today_weekday = today.weekday()
                        if community.weekdays and community.weekdays[0] != 'Other':
                            expected_weekday = weekday_map.get(community.weekdays[0])
                            if expected_weekday != today_weekday:
                                print(f'    ⚠️ 今日は{today.strftime("%A")}だが、weekdaysは{community.weekdays[0]}')
                    
        except Community.DoesNotExist:
            print(f'\n{name}: 見つかりません')
    
    # 修正が必要な集会のリスト
    print('\n\n' + '='*70)
    print('修正が必要な集会:')
    print('='*70)
    
    for name in communities:
        try:
            community = Community.objects.get(name=name)
            masters = Event.objects.filter(
                community=community,
                is_recurring_master=True
            ).select_related('recurrence_rule')
            
            for master in masters:
                if master.recurrence_rule and master.recurrence_rule.start_date and community.weekdays and community.weekdays[0] != 'Other':
                    expected_weekday = weekday_map.get(community.weekdays[0])
                    actual_weekday = master.recurrence_rule.start_date.weekday()
                    
                    if expected_weekday != actual_weekday:
                        print(f'\n{name}:')
                        print(f'  RecurrenceRule ID: {master.recurrence_rule.id}')
                        print(f'  現在のstart_date: {master.recurrence_rule.start_date} ({master.recurrence_rule.start_date.strftime("%A")})')
                        
                        # 正しい曜日の直近の日付を計算
                        days_ahead = expected_weekday - today.weekday()
                        if days_ahead < 0:
                            days_ahead += 7
                        correct_date = today + timedelta(days=days_ahead)
                        
                        print(f'  修正後のstart_date案: {correct_date} ({correct_date.strftime("%A")})')
                        
        except Community.DoesNotExist:
            pass


if __name__ == '__main__':
    check_weekdays_consistency()