#!/usr/bin/env python3
"""
隔週開催コミュニティの開催パターン分析スクリプト
"""
import csv
from datetime import datetime
from collections import defaultdict

# 対象コミュニティとそのID
target_communities = {
    75: "AI集会ゆる雑談Week",
    56: "ITエンジニア キャリア相談・雑談集会",
    20: "OSS集会（オープンソースソフトウェア集会）",
    71: "「計算と自然」集会",
    67: "だいたいで分かる政治経済",
    7: "アバターホビー集会",
    16: "アバター改変なんもわからん集会",
    3: "ゲーム開発集会Ⅲ",
    31: "シェーダー集会",
    54: "セキュリティ集会 in VRChat",
    18: "データサイエンティスト集会",
    77: "妖怪好き交流所『怪し火-AYASHIBI-』",
    39: "論文紹介集会"
}

def get_week_number(date_str):
    """日付から週番号を計算（2024年1月1日を第1週とする）"""
    date = datetime.strptime(date_str, "%Y-%m-%d")
    # 2024年1月1日（月曜日）を基準にする
    base_date = datetime(2024, 1, 1)
    days_diff = (date - base_date).days
    # 月曜日始まりの週番号を計算
    week_number = (days_diff + base_date.weekday()) // 7 + 1
    return week_number

def analyze_biweekly_pattern(csv_path):
    """CSVファイルから隔週開催パターンを分析"""
    community_dates = defaultdict(list)
    
    # CSVファイルを読み込む
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            community_id = int(row['community_id'])
            if community_id in target_communities:
                date = row['date']
                community_dates[community_id].append(date)
    
    # 各コミュニティの開催パターンを分析
    results = []
    for community_id, name in target_communities.items():
        dates = sorted(community_dates.get(community_id, []))
        if not dates:
            results.append({
                'name': name,
                'pattern': '開催データなし',
                'dates': []
            })
            continue
        
        # 週番号を計算
        week_numbers = [get_week_number(date) for date in dates]
        
        # 奇数週・偶数週の開催回数をカウント
        odd_weeks = sum(1 for week in week_numbers if week % 2 == 1)
        even_weeks = sum(1 for week in week_numbers if week % 2 == 0)
        
        # パターンを判定
        if odd_weeks > even_weeks:
            pattern = f"奇数週開催（奇数週: {odd_weeks}回, 偶数週: {even_weeks}回）"
        elif even_weeks > odd_weeks:
            pattern = f"偶数週開催（奇数週: {odd_weeks}回, 偶数週: {even_weeks}回）"
        else:
            pattern = f"不定（奇数週: {odd_weeks}回, 偶数週: {even_weeks}回）"
        
        # 6月の開催日と週番号を表示用に準備
        june_dates = [(date, get_week_number(date)) for date in dates if date.startswith('2024-06')]
        
        results.append({
            'name': name,
            'pattern': pattern,
            'dates': june_dates,
            'all_weeks': week_numbers
        })
    
    return results

def main():
    csv_path = "/Users/main/Downloads/hub-2025-06-1915-45-32.csv"
    results = analyze_biweekly_pattern(csv_path)
    
    # 結果を表示
    print("=== 隔週開催コミュニティの開催パターン分析結果 ===\n")
    
    for result in sorted(results, key=lambda x: x['name']):
        print(f"【{result['name']}】")
        print(f"  パターン: {result['pattern']}")
        
        if result['dates']:
            print("  2024年6月の開催日:")
            for date, week in result['dates']:
                weekday = datetime.strptime(date, "%Y-%m-%d").strftime("%a")
                print(f"    - {date} ({weekday}) 第{week}週")
        
        print()

if __name__ == "__main__":
    main()