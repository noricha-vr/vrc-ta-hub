#!/usr/bin/env python
"""コミュニティID 79を毎週開催に更新"""

import os
import sys
import django

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from event.models import RecurrenceRule, Event
from community.models import Community

def update_community_79_weekly():
    """コミュニティID 79を毎週開催に更新"""
    
    try:
        # コミュニティを取得
        community = Community.objects.get(id=79)
        print(f"=== {community.name} を毎週開催に更新 ===\n")
        
        # コミュニティの詳細を表示
        print(f"コミュニティ名: {community.name}")
        print(f"開催曜日: {community.weekdays}")
        print(f"開始時刻: {community.start_time}")
        print(f"現在の頻度: {community.frequency}")
        
        # frequencyフィールドを更新
        community.frequency = '毎週'
        community.save()
        print(f"\n✓ frequencyを「毎週」に更新しました")
        
        # RecurrenceRuleを確認・更新
        event = community.events.filter(is_recurring_master=True).first()
        
        if event and event.recurrence_rule:
            rule = event.recurrence_rule
            print(f"\n既存のRecurrenceRule:")
            print(f"  頻度: {rule.frequency}")
            print(f"  間隔: {rule.interval}")
            print(f"  カスタムルール: {rule.custom_rule}")
            
            # 毎週に更新
            rule.frequency = 'WEEKLY'
            rule.interval = 1
            rule.custom_rule = ''
            rule.save()
            print(f"\n✓ RecurrenceRuleを毎週に更新しました")
            
        else:
            # RecurrenceRuleが存在しない場合は作成
            print(f"\nRecurrenceRuleが存在しないため新規作成します")
            
            # 最新のイベントを取得
            latest_event = community.events.order_by('-date').first()
            
            if latest_event:
                # RecurrenceRuleを作成
                rule = RecurrenceRule.objects.create(
                    frequency='WEEKLY',
                    interval=1,
                    custom_rule=''
                )
                
                # イベントに紐付け
                latest_event.recurrence_rule = rule
                latest_event.is_recurring_master = True
                latest_event.save()
                
                print(f"✓ RecurrenceRuleを作成し、最新のイベント（{latest_event.date}）に紐付けました")
            else:
                print(f"✗ イベントが見つかりません")
        
        # 今後のイベントを生成
        print(f"\n今後のイベントを確認...")
        
        from datetime import timedelta
        from django.utils import timezone
        
        today = timezone.now().date()
        future_events = community.events.filter(date__gte=today).order_by('date')
        
        print(f"今後のイベント数: {future_events.count()}件")
        
        if future_events.count() < 13:  # 3ヶ月分（約13週）より少ない場合
            print(f"\nイベントが少ないため、追加生成が必要かもしれません")
            print(f"次回のイベント生成時に自動的に作成されます")
        
        print(f"\n=== 更新完了 ===")
        
    except Community.DoesNotExist:
        print(f"✗ コミュニティID 79が見つかりません")
    except Exception as e:
        print(f"✗ エラーが発生しました: {e}")

if __name__ == '__main__':
    update_community_79_weekly()