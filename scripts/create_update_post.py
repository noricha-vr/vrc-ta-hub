#!/usr/bin/env python
import os
import sys
import django
from datetime import datetime, timezone

# Djangoのセットアップ
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ta_hub.settings')
django.setup()

from news.models import Post, Category
from django.utils.text import slugify

def create_update_post():
    """アップデート記事を作成"""
    
    # アップデートカテゴリーを取得
    try:
        update_category = Category.objects.get(slug='update')
    except Category.DoesNotExist:
        print("アップデートカテゴリーが見つかりません")
        return
    
    # 記事データ
    title = "サイトのUI/UX改善とGoogleカレンダー連携機能を追加しました"
    slug = "2025-01-10-ui-improvements"
    
    # 既存記事をチェック
    if Post.objects.filter(slug=slug).exists():
        print(f"記事は既に存在します: {slug}")
        return
    
    # Markdownファイルを読み込み
    with open('/app/news/fixtures/2025-01-10-ui-improvements.md', 'r') as f:
        body_markdown = f.read()
    
    post = Post.objects.create(
        title=title,
        slug=slug,
        body_markdown=body_markdown,
        meta_description="フッターデザイン刷新、Googleカレンダー連携機能追加、ナビゲーション最適化などサイトのUI/UX改善を実施しました。",
        category=update_category,
        is_published=True,
        published_at=datetime.now(timezone.utc)
    )
    print(f"記事を作成しました: {post.title}")

if __name__ == '__main__':
    create_update_post()