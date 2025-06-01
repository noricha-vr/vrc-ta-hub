from django.utils import timezone

from community.models import Community
from event.models import Event, EventDetail

# 今日の日付を取得
today = timezone.now().date()

# 適当なコミュニティを取得
community = Community.objects.first()

if community:
    # 今日のイベントを作成（存在しない場合）
    event, created = Event.objects.get_or_create(
        community=community,
        date=today,
        start_time='22:00',
        defaults={
            'duration': 60,
            'weekday': today.strftime("%a"),
        }
    )

    # 特別企画を作成
    special_event, created = EventDetail.objects.get_or_create(
        event=event,
        detail_type='SPECIAL',
        theme='年末特別企画：2024年の技術振り返り',
        defaults={
            'start_time': '22:00',
            'duration': 120,
            'h1': '【特別企画】年末特別企画：2024年の技術振り返り',
            'meta_description': 'VRChat技術学術系集会の年末特別企画！2024年の技術トレンドを振り返り、来年の展望を語り合います。',
            'contents': '''# 年末特別企画：2024年の技術振り返り

## イベント概要

2024年も残りわずか！今年一年のVRChat技術学術系集会での活動を振り返り、
来年に向けての展望を語り合う特別企画を開催します。

## プログラム

- 22:00-22:30 オープニング＆今年の振り返り
- 22:30-23:30 パネルディスカッション：2024年の技術トレンド
- 23:30-24:00 来年の展望＆クロージング

## 参加方法

いつもの集会と同じようにJoinしてください！
初めての方も大歓迎です。
''',
        }
    )

    # ブログ記事を作成
    blog_post, created = EventDetail.objects.get_or_create(
        event=event,
        detail_type='BLOG',
        theme='VRChat技術学術系集会の歴史と今後',
        defaults={
            'start_time': '22:30',
            'duration': 30,
            'h1': 'VRChat技術学術系集会の歴史と今後の展望',
            'meta_description': 'VRChat技術学術系集会の設立から現在までの歩みと、今後の展望について詳しく解説します。',
            'contents': '''# VRChat技術学術系集会の歴史と今後の展望

## はじめに

VRChat技術学術系集会は、技術や学問に関心を持つ人々が集まり、
知識を共有し合うコミュニティとして発展してきました。

## これまでの歩み

### 設立期（2020年〜）

最初は小さな集まりからスタートしました...

### 成長期（2022年〜）

参加者が増え、様々な分野の専門家が集まるようになりました...

### 現在（2024年）

毎日どこかで集会が開催され、活発な技術交流が行われています。

## 今後の展望

- より多様な分野への拡大
- 国際的な交流の促進
- 新技術の積極的な活用

皆様のご参加をお待ちしています！
''',
        }
    )

    print(f"テストデータを作成しました:")
    print(f"- イベント: {event}")
    print(f"- 特別企画: {special_event} (新規作成: {created})")
    print(f"- ブログ記事: {blog_post} (新規作成: {created})")
else:
    print("コミュニティが見つかりません。先にコミュニティを作成してください。")
