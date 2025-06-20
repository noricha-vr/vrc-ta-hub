#!/usr/bin/env python
"""LLMの履歴を使った予測精度テスト"""

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

def test_llm_prediction_with_history():
    """過去の開催履歴を使ったLLM予測のテスト"""
    
    print("=== LLM予測精度テスト（開催履歴使用） ===\n")
    
    # カスタムルール（OTHER）のコミュニティをテスト
    test_communities = [
        {
            'id': 52,
            'name': 'C# Tokyo もくもく会',
            'rule': '2～3週間に1度',
            'description': '不規則な間隔での開催'
        },
        {
            'id': 51,
            'name': 'VRC MED J SALON',
            'rule': '毎月',
            'description': '月1回開催（曜日と週の傾向あり）'
        },
        {
            'id': 42,
            'name': 'VRC歴史集会',
            'rule': 'セミナー会：毎月第一月曜日、雑談会：毎月第三水曜日',
            'description': '複数パターンの開催'
        },
        {
            'id': 71,
            'name': '「計算と自然」集会',
            'rule': '隔週（第1・第3土曜日）',
            'description': '隔週パターン'
        }
    ]
    
    service = RecurrenceService()
    today = timezone.now().date()
    
    for test_info in test_communities:
        community_id = test_info['id']
        name = test_info['name']
        
        try:
            community = Community.objects.get(id=community_id)
            
            # 過去のイベント履歴を取得
            past_events = Event.objects.filter(
                community=community,
                date__lt=today
            ).order_by('-date')[:10]
            
            if not past_events:
                print(f"⚠️ {name}: 過去のイベント履歴がありません")
                continue
            
            print(f"\n{name} (ID: {community_id}):")
            print(f"  ルール: {test_info['rule']}")
            print(f"  説明: {test_info['description']}")
            
            # 過去の履歴を表示
            print(f"\n  過去の開催履歴（直近5回）:")
            for event in past_events[:5]:
                weekday = ['月', '火', '水', '木', '金', '土', '日'][event.date.weekday()]
                week = ((event.date.day - 1) + event.date.replace(day=1).weekday()) // 7 + 1
                print(f"    {event.date.strftime('%Y-%m-%d')} ({weekday}) 第{week}週")
            
            # マスターイベントを探す
            master_event = community.events.filter(is_recurring_master=True).first()
            if not master_event:
                # なければ最新のイベントを基準にする
                master_event = past_events[0]
            
            # カスタムルールを作成（テスト用）
            rule = RecurrenceRule(
                frequency='OTHER',
                custom_rule=test_info['rule']
            )
            
            # LLMで予測
            print(f"\n  LLMによる予測（今後1ヶ月）:")
            predicted_dates = service.generate_dates(
                rule=rule,
                base_date=today,
                base_time=master_event.start_time,
                months=1,
                community=community
            )
            
            for date in predicted_dates[:5]:
                weekday = ['月', '火', '水', '木', '金', '土', '日'][date.weekday()]
                week = ((date.day - 1) + date.replace(day=1).weekday()) // 7 + 1
                print(f"    {date.strftime('%Y-%m-%d')} ({weekday}) 第{week}週")
            
            # パターン分析
            if len(past_events) >= 2:
                # 間隔の計算
                intervals = []
                for i in range(min(4, len(past_events) - 1)):
                    interval = (past_events[i].date - past_events[i + 1].date).days
                    intervals.append(interval)
                
                if intervals:
                    avg_interval = sum(intervals) / len(intervals)
                    print(f"\n  分析:")
                    print(f"    平均開催間隔: {avg_interval:.1f}日")
                    
                    # 予測の間隔を計算
                    if len(predicted_dates) >= 2:
                        pred_intervals = []
                        for i in range(len(predicted_dates) - 1):
                            interval = (predicted_dates[i + 1] - predicted_dates[i]).days
                            pred_intervals.append(interval)
                        
                        pred_avg = sum(pred_intervals) / len(pred_intervals)
                        print(f"    予測の平均間隔: {pred_avg:.1f}日")
                        
                        # 精度評価
                        diff = abs(pred_avg - avg_interval)
                        if diff <= 2:
                            print(f"    評価: ✓ 良好（差分 {diff:.1f}日）")
                        else:
                            print(f"    評価: △ 要改善（差分 {diff:.1f}日）")
            
        except Community.DoesNotExist:
            print(f"\n{name} (ID: {community_id}): コミュニティが見つかりません")
        except Exception as e:
            print(f"\n{name} (ID: {community_id}): エラー - {e}")
            import traceback
            traceback.print_exc()
    
    print("\n=== テスト完了 ===")

if __name__ == '__main__':
    test_llm_prediction_with_history()