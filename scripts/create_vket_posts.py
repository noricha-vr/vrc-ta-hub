#!/usr/bin/env python
import os
import sys
import django
from datetime import datetime, timezone, timedelta

# Djangoのセットアップ
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ta_hub.settings')
django.setup()

from news.models import Post, Category
from django.utils.text import slugify

def create_vket_posts():
    """Vket関連の記事を作成"""
    
    # 活動履歴カテゴリーを取得
    try:
        activity_category = Category.objects.get(slug='activity')
    except Category.DoesNotExist:
        print("活動履歴カテゴリーが見つかりません")
        return
    
    # 記事データ
    posts_data = [
        {
            'title': 'VRC技術・学術系イベントHUB × Vketステージ コラボ【Vket技術学術WEEK】開催決定！',
            'slug': '2025-07-04-vket-week-announcement',
            'markdown_file': '/app/news/fixtures/2025-07-04-vket-week-announcement.md',
            'meta_description': 'VirtualMarketとのコラボで16日間連続・20団体が登壇するVket技術学術WEEKの開催が決定！',
            'published_at': datetime(2025, 7, 4, 19, 52, tzinfo=timezone.utc),  # 画像のツイート時刻
        },
        {
            'title': 'Vket技術学術WEEK 動画アーカイブまとめ',
            'slug': '2025-01-10-vket-week-videos', 
            'markdown_file': '/app/news/fixtures/2025-01-10-vket-week-videos.md',
            'meta_description': '2025年7月12日〜27日開催のVket技術学術WEEK全20団体の発表動画アーカイブまとめ',
            'published_at': datetime.now(timezone.utc),  # 今日の日付
        }
    ]
    
    for data in posts_data:
        # 既存記事をチェック
        if Post.objects.filter(slug=data['slug']).exists():
            print(f"記事は既に存在します: {data['slug']}")
            continue
        
        # Markdownファイルを読み込み
        try:
            with open(data['markdown_file'], 'r') as f:
                body_markdown = f.read()
        except FileNotFoundError:
            print(f"ファイルが見つかりません: {data['markdown_file']}")
            continue
        
        post = Post.objects.create(
            title=data['title'],
            slug=data['slug'],
            body_markdown=body_markdown,
            meta_description=data['meta_description'],
            category=activity_category,
            is_published=True,
            published_at=data['published_at']
        )
        print(f"記事を作成しました: {post.title}")
    
    print("\nVket関連記事の作成が完了しました")

if __name__ == '__main__':
    create_vket_posts()