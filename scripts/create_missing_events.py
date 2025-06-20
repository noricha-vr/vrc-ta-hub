#!/usr/bin/env python
"""イベントが存在しないコミュニティに新規イベントを作成"""

import os
import sys
import django
from datetime import datetime, date, time

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from event.models import RecurrenceRule, Event
from community.models import Community

def get_week_number(target_date):
    """日付から週番号を取得（1月1日を第1週として）"""
    year_start = date(target_date.year, 1, 1)
    days_diff = (target_date - year_start).days
    return (days_diff // 7) + 1

def create_missing_events():
    """イベントが存在しないコミュニティに新規イベントを作成"""
    
    print("=== 不足しているイベントの作成 ===\n")
    
    # 1. アバター改変なんもわからん集会 - 6月25日(水)基準
    try:
        print("1. アバター改変なんもわからん集会")
        community = Community.objects.get(name='アバター改変なんもわからん集会')
        
        # 2025年6月25日の週番号を確認
        base_date = date(2025, 6, 25)
        week_num = get_week_number(base_date)
        pattern = 'biweekly_A' if week_num % 2 == 1 else 'biweekly_B'
        
        # RecurrenceRuleを作成
        rule = RecurrenceRule.objects.create(
            frequency='WEEKLY',
            interval=2,
            custom_rule=pattern
        )
        
        # イベントを作成
        event = Event.objects.create(
            community=community,
            date=base_date,
            start_time=time(21, 0),  # デフォルト時刻
            duration=60,
            is_recurring_master=True,
            recurrence_rule=rule
        )
        
        print(f"✓ イベント作成完了 - {pattern}（2025/6/25は第{week_num}週）")
        
    except Community.DoesNotExist:
        print("✗ コミュニティが見つかりません")
    except Exception as e:
        print(f"✗ エラー: {e}")
    
    # 2. シェーダー集会 - 6月16日(月)基準
    try:
        print("\n2. シェーダー集会")
        community = Community.objects.get(name='シェーダー集会')
        
        # 2025年6月16日の週番号を確認
        base_date = date(2025, 6, 16)
        week_num = get_week_number(base_date)
        pattern = 'biweekly_A' if week_num % 2 == 1 else 'biweekly_B'
        
        # RecurrenceRuleを作成
        rule = RecurrenceRule.objects.create(
            frequency='WEEKLY',
            interval=2,
            custom_rule=pattern
        )
        
        # イベントを作成
        event = Event.objects.create(
            community=community,
            date=base_date,
            start_time=time(21, 0),  # デフォルト時刻
            duration=60,
            is_recurring_master=True,
            recurrence_rule=rule
        )
        
        print(f"✓ イベント作成完了 - {pattern}（2025/6/16は第{week_num}週）")
        
    except Community.DoesNotExist:
        print("✗ コミュニティが見つかりません")
    except Exception as e:
        print(f"✗ エラー: {e}")
    
    print("\n作成が完了しました")

if __name__ == '__main__':
    create_missing_events()