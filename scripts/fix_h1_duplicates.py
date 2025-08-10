#!/usr/bin/env python
"""
H1タグの重複を修正するスクリプト
NewsとEventDetailの本文から最初の # 行を削除
"""
import os
import sys
import django

# Djangoのセットアップ
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ta_hub.settings')
django.setup()

from news.models import Post
from event.models import EventDetail


def fix_h1_duplicates():
    """NewsとEventDetailのH1重複を修正"""
    
    print("=== H1重複修正開始 ===\n")
    
    # News記事のH1削除
    print("News記事の修正...")
    news_fixed = 0
    posts = Post.objects.all()
    for post in posts:
        lines = post.body_markdown.split('\n')
        if lines and lines[0].strip().startswith('# '):
            original_first_line = lines[0]
            post.body_markdown = '\n'.join(lines[1:]).lstrip()
            post.save()
            print(f"  Fixed News: {post.slug}")
            print(f"    削除した行: {original_first_line[:50]}...")
            news_fixed += 1
    
    print(f"\nNews記事: {news_fixed}件修正\n")
    
    # EventDetailのH1削除
    print("EventDetailの修正...")
    event_fixed = 0
    event_details = EventDetail.objects.exclude(contents='').exclude(contents__isnull=True)
    for ed in event_details:
        if ed.contents and ed.contents.strip().startswith('# '):
            lines = ed.contents.split('\n')
            original_first_line = lines[0]
            ed.contents = '\n'.join(lines[1:]).lstrip()
            ed.save()
            print(f"  Fixed EventDetail ID {ed.id}: {ed.title[:30]}...")
            print(f"    削除した行: {original_first_line[:50]}...")
            event_fixed += 1
    
    print(f"\nEventDetail: {event_fixed}件修正")
    
    print(f"\n=== 修正完了 ===")
    print(f"合計: {news_fixed + event_fixed}件のH1重複を修正しました")


if __name__ == '__main__':
    fix_h1_duplicates()