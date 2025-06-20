#!/usr/bin/env python
"""アクティブな集会の情報をCSVファイルにエクスポート"""

import os
import sys
import django
import csv
from datetime import datetime

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.utils import timezone
from community.models import Community
from event.models import Event, RecurrenceRule

def export_active_communities():
    """アクティブな集会の情報をCSVファイルにエクスポート"""
    
    print("=== アクティブな集会情報のエクスポート ===\n")
    
    # 終了していない、承認済みの集会を取得
    communities = Community.objects.filter(
        status='approved',
        end_at__isnull=True  # 終了日が設定されていない
    ).order_by('name')
    
    print(f"対象集会数: {communities.count()}件\n")
    
    # CSVファイルのパス
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_filename = f'/opt/project/scripts/active_communities_{timestamp}.csv'
    
    # CSVファイルに書き込み
    with open(csv_filename, 'w', newline='', encoding='utf-8-sig') as csvfile:
        fieldnames = [
            'ID',
            '集会名',
            '開催曜日',
            '開始時刻',
            '開催時間（分）',
            '開催周期',
            'RecurrenceRule頻度',
            'RecurrenceRule間隔',
            'RecurrenceRuleカスタム',
            '直近の最終開催日',
            '今後の次回開催日',
            'プラットフォーム',
            'タグ'
        ]
        
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        
        for community in communities:
            # 開催周期の情報を取得
            recurrence_info = get_recurrence_info(community)
            
            # 直近の最終開催日を取得
            last_event = community.events.filter(
                date__lte=timezone.now().date()
            ).order_by('-date').first()
            
            # 今後の次回開催日を取得
            next_event = community.events.filter(
                date__gt=timezone.now().date()
            ).order_by('date').first()
            
            # 曜日情報を整形
            weekdays = eval(community.weekdays) if isinstance(community.weekdays, str) else community.weekdays
            weekday_str = ', '.join(weekdays) if weekdays else ''
            
            # タグ情報を整形
            tags = eval(community.tags) if isinstance(community.tags, str) else community.tags
            tags_str = ', '.join(tags) if tags else ''
            
            row = {
                'ID': community.id,
                '集会名': community.name,
                '開催曜日': weekday_str,
                '開始時刻': community.start_time.strftime('%H:%M') if community.start_time else '',
                '開催時間（分）': community.duration,
                '開催周期': community.frequency,
                'RecurrenceRule頻度': recurrence_info['frequency'],
                'RecurrenceRule間隔': recurrence_info['interval'],
                'RecurrenceRuleカスタム': recurrence_info['custom_rule'],
                '直近の最終開催日': last_event.date.strftime('%Y-%m-%d') if last_event else '開催履歴なし',
                '今後の次回開催日': next_event.date.strftime('%Y-%m-%d') if next_event else '予定なし',
                'プラットフォーム': community.platform,
                'タグ': tags_str
            }
            
            writer.writerow(row)
            
            # 進捗表示
            if community.id % 10 == 0:
                print(f"処理中: {community.id} - {community.name}")
    
    print(f"\n✓ CSVファイルを作成しました: {csv_filename}")
    
    # ファイルサイズを確認
    file_size = os.path.getsize(csv_filename)
    print(f"ファイルサイズ: {file_size:,} bytes")
    
    # サマリー情報
    print("\n=== サマリー ===")
    
    # 開催周期別の集計
    frequency_counts = {}
    for community in communities:
        freq = community.frequency
        frequency_counts[freq] = frequency_counts.get(freq, 0) + 1
    
    print("\n開催周期別集計:")
    for freq, count in sorted(frequency_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"  {freq}: {count}件")
    
    # 最近開催されていない集会をリストアップ
    print("\n最近開催されていない集会（90日以上）:")
    stale_count = 0
    
    for community in communities[:10]:  # 最初の10件のみ表示
        last_event = community.events.filter(
            date__lte=timezone.now().date()
        ).order_by('-date').first()
        
        if last_event:
            days_since = (timezone.now().date() - last_event.date).days
            if days_since > 90:
                print(f"  - {community.name}: {days_since}日前（{last_event.date}）")
                stale_count += 1
        else:
            print(f"  - {community.name}: 開催履歴なし")
            stale_count += 1
    
    if stale_count >= 10:
        print(f"  ... 他多数")
    
    return csv_filename

def get_recurrence_info(community):
    """コミュニティのRecurrenceRule情報を取得"""
    
    # RecurrenceRuleを持つイベントを探す
    event = community.events.filter(is_recurring_master=True).first()
    
    if event and event.recurrence_rule:
        rule = event.recurrence_rule
        return {
            'frequency': rule.frequency,
            'interval': rule.interval,
            'custom_rule': rule.custom_rule or ''
        }
    else:
        return {
            'frequency': '',
            'interval': '',
            'custom_rule': ''
        }

if __name__ == '__main__':
    csv_file = export_active_communities()
    
    # ホストマシンにコピーするためのコマンドを表示
    print(f"\nホストマシンにコピーするには:")
    print(f"docker cp vrc-ta-hub:{csv_file} ./active_communities.csv")