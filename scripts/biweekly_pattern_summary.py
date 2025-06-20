#!/usr/bin/env python3
"""
隔週開催パターンの要約を表示
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
    base_date = datetime(2024, 1, 1)
    days_diff = (date - base_date).days
    week_number = (days_diff + base_date.weekday()) // 7 + 1
    return week_number

def main():
    csv_path = "/Users/main/Downloads/hub-2025-06-1915-45-32.csv"
    community_dates = defaultdict(list)
    
    # CSVファイルを読み込む
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            community_id = int(row['community_id'])
            if community_id in target_communities:
                date = row['date']
                community_dates[community_id].append(date)
    
    # 奇数週と偶数週のグループに分類
    odd_week_communities = []
    even_week_communities = []
    irregular_communities = []
    no_data_communities = []
    
    for community_id, name in target_communities.items():
        dates = sorted(community_dates.get(community_id, []))
        if not dates:
            no_data_communities.append(name)
            continue
        
        # 週番号を計算
        week_numbers = [get_week_number(date) for date in dates]
        
        # 奇数週・偶数週の開催回数をカウント
        odd_weeks = sum(1 for week in week_numbers if week % 2 == 1)
        even_weeks = sum(1 for week in week_numbers if week % 2 == 0)
        total = odd_weeks + even_weeks
        
        # パターンを判定（90%以上の偏りがある場合のみ明確に分類）
        if total > 0:
            odd_ratio = odd_weeks / total
            if odd_ratio >= 0.9:
                odd_week_communities.append((name, odd_weeks, even_weeks))
            elif odd_ratio <= 0.1:
                even_week_communities.append((name, odd_weeks, even_weeks))
            else:
                irregular_communities.append((name, odd_weeks, even_weeks))
    
    # 結果を表示
    print("=== 隔週開催コミュニティのグループ分類 ===\n")
    
    print("【奇数週開催グループ】")
    for name, odd, even in sorted(odd_week_communities):
        print(f"  - {name} (奇数週{odd}回、偶数週{even}回)")
    
    print("\n【偶数週開催グループ】")
    for name, odd, even in sorted(even_week_communities):
        print(f"  - {name} (奇数週{odd}回、偶数週{even}回)")
    
    print("\n【不規則開催】")
    for name, odd, even in sorted(irregular_communities):
        ratio = f"奇数週{odd}回({odd/(odd+even)*100:.0f}%)、偶数週{even}回({even/(odd+even)*100:.0f}%)"
        print(f"  - {name} ({ratio})")
    
    if no_data_communities:
        print("\n【開催データなし】")
        for name in sorted(no_data_communities):
            print(f"  - {name}")
    
    print("\n=== まとめ ===")
    print(f"奇数週開催: {len(odd_week_communities)}コミュニティ")
    print(f"偶数週開催: {len(even_week_communities)}コミュニティ")
    print(f"不規則開催: {len(irregular_communities)}コミュニティ")
    print(f"データなし: {len(no_data_communities)}コミュニティ")

if __name__ == "__main__":
    main()