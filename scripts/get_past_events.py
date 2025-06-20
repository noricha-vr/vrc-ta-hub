#!/usr/bin/env python
import os
import sys
import django
from datetime import datetime

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ta_hub.settings')
django.setup()

from event.models import Event
from community.models import Community
from django.utils import timezone

def get_past_events_for_communities():
    """指定された集会の過去のイベント日付を取得"""
    
    # 対象の集会名
    target_communities = [
        "VRC MED J SALON",
        "VR研究Cafe", 
        "分散システム集会"
    ]
    
    results = {}
    
    for community_name in target_communities:
        try:
            # コミュニティを取得
            community = Community.objects.get(name=community_name)
            
            # 過去のイベントを取得（開催日が現在より前のもの）
            past_events = Event.objects.filter(
                community=community,
                date__lt=timezone.now().date()
            ).order_by('-date', '-start_time')
            
            if past_events.exists():
                latest_event = past_events.first()
                results[community_name] = {
                    'community_id': community.id,
                    'latest_event_date': latest_event.date,
                    'latest_event_time': latest_event.start_time,
                    'latest_event_title': f"{latest_event.community.name} - {latest_event.date}",
                    'total_past_events': past_events.count(),
                    'all_past_dates': [
                        {
                            'date': event.date,
                            'time': event.start_time,
                            'title': f"{event.community.name} - {event.date}"
                        } for event in past_events[:5]  # 最新5件のみ表示
                    ]
                }
            else:
                results[community_name] = {
                    'community_id': community.id,
                    'message': '過去のイベントが見つかりません'
                }
                
        except Community.DoesNotExist:
            results[community_name] = {
                'error': f'コミュニティ "{community_name}" が見つかりません'
            }
    
    # 結果を表示
    print("\n=== 過去のイベント日付取得結果 ===\n")
    
    for community_name, data in results.items():
        print(f"【{community_name}】")
        
        if 'error' in data:
            print(f"  エラー: {data['error']}")
        elif 'message' in data:
            print(f"  コミュニティID: {data['community_id']}")
            print(f"  {data['message']}")
        else:
            print(f"  コミュニティID: {data['community_id']}")
            print(f"  最新の過去イベント: {data['latest_event_date'].strftime('%Y-%m-%d')} {data['latest_event_time'].strftime('%H:%M')}")
            print(f"  イベントタイトル: {data['latest_event_title']}")
            print(f"  過去のイベント総数: {data['total_past_events']}")
            print("\n  直近5件の過去イベント:")
            for i, event in enumerate(data['all_past_dates'], 1):
                print(f"    {i}. {event['date'].strftime('%Y-%m-%d')} {event['time'].strftime('%H:%M')} - {event['title']}")
        
        print("\n" + "-" * 50 + "\n")
    
    # RecurrenceRule用のstart_date候補を表示
    print("\n=== RecurrenceRule start_date 候補 ===\n")
    for community_name, data in results.items():
        if 'latest_event_date' in data:
            print(f"{community_name}:")
            print(f"  start_date = '{data['latest_event_date'].strftime('%Y-%m-%d')}'")
    
    return results

if __name__ == "__main__":
    get_past_events_for_communities()