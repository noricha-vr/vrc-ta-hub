#!/usr/bin/env python
"""未来のイベントを削除して新しいstart_dateで再生成"""

import os
import sys
import django
from datetime import datetime, timedelta

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.utils import timezone
from django.db import transaction
from event.models import Event, RecurrenceRule
from event.recurrence_service import RecurrenceService
from community.models import Community

def recreate_future_events():
    """未来のイベントを削除して新しいstart_dateで再生成"""
    
    print("=== 未来のイベントを再生成 ===\n")
    
    today = timezone.now().date()
    
    # 定期ルールを持つマスターイベントを取得
    recurring_masters = Event.objects.filter(
        is_recurring_master=True,
        recurrence_rule__isnull=False
    ).select_related('recurrence_rule', 'community')
    
    print(f"処理対象: {recurring_masters.count()}件\n")
    
    service = RecurrenceService()
    total_deleted = 0
    total_created = 0
    
    for master_event in recurring_masters:
        rule = master_event.recurrence_rule
        community = master_event.community
        
        # start_dateが設定されていない場合はスキップ
        if not rule.start_date:
            print(f"⚠️ {community.name}: start_dateが未設定のためスキップ")
            continue
        
        with transaction.atomic():
            # 未来のイベントを削除（マスターイベント以外）
            future_events = Event.objects.filter(
                community=community,
                date__gt=today,
                is_recurring_master=False
            )
            deleted_count = future_events.count()
            future_events.delete()
            
            # 新しいイベントを生成（3ヶ月分）
            dates = service.generate_dates(
                rule=rule,
                base_date=today,
                base_time=master_event.start_time,
                months=3
            )
            
            created_count = 0
            for date in dates:
                if date > today:
                    # 既存チェック（念のため）
                    exists = Event.objects.filter(
                        community=community,
                        date=date,
                        start_time=master_event.start_time
                    ).exists()
                    
                    if not exists:
                        Event.objects.create(
                            community=community,
                            date=date,
                            start_time=master_event.start_time,
                            duration=master_event.duration,
                            weekday=date.strftime('%a').upper()[:3],
                            recurring_master=master_event
                        )
                        created_count += 1
            
            if deleted_count > 0 or created_count > 0:
                print(f"✓ {community.name}:")
                print(f"  start_date: {rule.start_date} ({rule.start_date.strftime('%A')})")
                print(f"  頻度: {rule.get_frequency_display()} (間隔: {rule.interval})")
                print(f"  削除: {deleted_count}件")
                print(f"  作成: {created_count}件")
                
                # 次回開催日を表示
                next_events = Event.objects.filter(
                    community=community,
                    date__gt=today
                ).order_by('date')[:3]
                
                if next_events:
                    print(f"  次回開催: {', '.join(e.date.strftime('%Y-%m-%d') for e in next_events)}")
                print()
            
            total_deleted += deleted_count
            total_created += created_count
    
    print(f"\n=== 再生成完了 ===")
    print(f"削除総数: {total_deleted}件")
    print(f"作成総数: {total_created}件")
    
    # 検証
    print("\n=== 検証 ===")
    
    test_communities = [
        (75, "AI集会ゆる雑談Week"),
        (76, "AI集会テックWeek"),
        (11, "CS集会"),
    ]
    
    for community_id, name in test_communities:
        try:
            community = Community.objects.get(id=community_id)
            master_event = community.events.filter(is_recurring_master=True).first()
            
            if master_event and master_event.recurrence_rule:
                rule = master_event.recurrence_rule
                
                # 次回開催日を取得
                next_events = community.events.filter(
                    date__gt=today
                ).order_by('date')[:5]
                
                print(f"\n{name}:")
                print(f"  start_date: {rule.start_date}")
                print(f"  次回開催日:")
                
                for event in next_events:
                    # start_dateからの週数を計算
                    if rule.start_date:
                        weeks_diff = (event.date - rule.start_date).days // 7
                        print(f"    {event.date} ({event.date.strftime('%a')}) - {weeks_diff}週後")
                
        except Community.DoesNotExist:
            print(f"\n{name}: コミュニティが見つかりません")

if __name__ == '__main__':
    recreate_future_events()