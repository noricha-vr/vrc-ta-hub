#!/usr/bin/env python
"""コミュニティID 79のイベントを生成"""

import os
import sys
import django
from datetime import datetime, timedelta

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.utils import timezone
from event.models import Event
from community.models import Community

def generate_events_for_community_79():
    """コミュニティID 79のイベントを生成"""
    
    try:
        # コミュニティを取得
        community = Community.objects.get(id=79)
        print(f"=== {community.name} のイベントを生成 ===\n")
        
        # 開催情報
        print(f"開催曜日: {community.weekdays}")
        print(f"開始時刻: {community.start_time}")
        print(f"開催時間: {community.duration}分")
        
        # 今日から3ヶ月分のイベントを生成
        today = timezone.now().date()
        end_date = today + timedelta(days=90)
        
        # 次の金曜日を探す
        current_date = today
        while current_date.weekday() != 4:  # 0=月曜日, 4=金曜日
            current_date += timedelta(days=1)
        
        created_count = 0
        
        print(f"\n{current_date}から{end_date}までのイベントを生成します...")
        
        # 毎週金曜日のイベントを生成
        while current_date <= end_date:
            # 既存のイベントがないか確認
            existing = Event.objects.filter(
                community=community,
                date=current_date
            ).exists()
            
            if not existing:
                # イベントを作成
                event = Event.objects.create(
                    community=community,
                    date=current_date,
                    start_time=community.start_time,
                    duration=community.duration,
                    is_recurring_master=False  # 個別イベント
                )
                created_count += 1
                print(f"✓ {current_date} {community.start_time} のイベントを作成")
            else:
                print(f"- {current_date} は既に存在します")
            
            # 次週へ
            current_date += timedelta(days=7)
        
        print(f"\n{created_count}件のイベントを作成しました")
        
        # Googleカレンダーへの同期
        print(f"\nGoogleカレンダーへの同期が必要です")
        print(f"同期コマンド: python /opt/project/scripts/simple_sync.py")
        
    except Community.DoesNotExist:
        print(f"✗ コミュニティID 79が見つかりません")
    except Exception as e:
        print(f"✗ エラーが発生しました: {e}")

if __name__ == '__main__':
    generate_events_for_community_79()