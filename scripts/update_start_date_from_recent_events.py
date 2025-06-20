#!/usr/bin/env python
"""直近の該当曜日で開催された日付をstart_dateにセット"""

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
from community.models import Community

def update_start_date_from_recent_events():
    """直近の該当曜日で開催された日付をstart_dateにセット"""
    
    print("=== 直近の開催日をstart_dateに設定 ===\n")
    
    # 定期ルールを持つイベントを取得
    recurring_masters = Event.objects.filter(
        is_recurring_master=True,
        recurrence_rule__isnull=False
    ).select_related('recurrence_rule', 'community')
    
    print(f"処理対象: {recurring_masters.count()}件\n")
    
    updated_count = 0
    today = timezone.now().date()
    
    for master_event in recurring_masters:
        rule = master_event.recurrence_rule
        community = master_event.community
        
        # 該当する曜日を取得（マスターイベントの曜日）
        target_weekday = master_event.date.weekday()
        
        # 直近でその曜日に開催されたイベントを検索
        # まず全てのイベントを取得してPythonで曜日をフィルタリング
        all_events = community.events.filter(
            date__lte=today
        ).order_by('-date')
        
        recent_event = None
        for event in all_events:
            if event.date.weekday() == target_weekday:
                recent_event = event
                break
        
        if recent_event:
            old_start_date = rule.start_date
            new_start_date = recent_event.date
            
            # 変更がある場合のみ更新
            if old_start_date != new_start_date:
                rule.start_date = new_start_date
                rule.save()
                
                print(f"✓ {community.name}:")
                print(f"  曜日: {recent_event.date.strftime('%A')}")
                print(f"  変更前: {old_start_date}")
                print(f"  変更後: {new_start_date} (直近の開催日)")
                print(f"  頻度: {rule.get_frequency_display()} (間隔: {rule.interval})")
                
                # 隔週の場合、次回開催予定日を表示
                if rule.frequency == 'WEEKLY' and rule.interval == 2:
                    next_dates = []
                    current_date = new_start_date
                    for _ in range(3):
                        current_date += timedelta(weeks=2)
                        if current_date > today:
                            next_dates.append(current_date)
                    
                    if next_dates:
                        print(f"  次回開催予定: {', '.join(d.strftime('%Y-%m-%d') for d in next_dates[:2])}")
                
                updated_count += 1
                print()
        else:
            print(f"⚠️ {community.name}: 該当曜日の開催履歴が見つかりません")
    
    print(f"\n=== 更新完了 ===")
    print(f"更新件数: {updated_count}件")
    
    # 更新後の検証
    print("\n=== 更新後の検証 ===")
    
    # 特に重要なコミュニティを検証
    test_communities = [
        (75, "AI集会ゆる雑談Week"),      # 月曜隔週
        (76, "AI集会テックWeek"),        # 月曜隔週
        (11, "CS集会"),                  # 火曜隔週
        (26, "株式投資座談会"),          # 土曜毎週
        (25, "量子力学"),                # 土曜隔週
    ]
    
    for community_id, name in test_communities:
        try:
            community = Community.objects.get(id=community_id)
            master_event = community.events.filter(is_recurring_master=True).first()
            
            if master_event and master_event.recurrence_rule:
                rule = master_event.recurrence_rule
                
                # 直近の開催日を取得
                recent_event = community.events.filter(
                    date__lte=today
                ).order_by('-date').first()
                
                print(f"\n{name} (ID: {community_id}):")
                print(f"  開催曜日: {master_event.date.strftime('%A')}")
                print(f"  頻度: {rule.get_frequency_display()} (間隔: {rule.interval})")
                print(f"  start_date: {rule.start_date}")
                print(f"  直近の開催: {recent_event.date if recent_event else 'なし'}")
                
                # start_dateと直近の開催日の曜日が一致しているか確認
                if rule.start_date and recent_event:
                    if rule.start_date.weekday() == recent_event.date.weekday():
                        print(f"  曜日の一致: ✓")
                    else:
                        print(f"  曜日の一致: ✗ (start_date: {rule.start_date.strftime('%A')}, 直近: {recent_event.date.strftime('%A')})")
                
        except Community.DoesNotExist:
            print(f"\n{name} (ID: {community_id}): コミュニティが見つかりません")

if __name__ == '__main__':
    update_start_date_from_recent_events()