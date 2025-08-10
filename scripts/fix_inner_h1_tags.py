#!/usr/bin/env python
"""
本文内のH1タグをH2に変換するスクリプト
EventDetailのcontents内にある # をすべて ## に変換
"""
import os
import sys
import django

# Djangoのセットアップ
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ta_hub.settings')
django.setup()

from event.models import EventDetail


def fix_inner_h1_tags():
    """EventDetailの本文内のH1をH2に変換"""
    
    print("=== 本文内のH1タグ修正開始 ===\n")
    
    # EventDetailの本文内のH1をH2に変換
    print("EventDetailの本文内H1をH2に変換...")
    fixed_count = 0
    event_details = EventDetail.objects.exclude(contents='').exclude(contents__isnull=True)
    
    for ed in event_details:
        if not ed.contents:
            continue
            
        lines = ed.contents.split('\n')
        new_lines = []
        has_h1 = False
        
        for line in lines:
            # 行頭の # で始まる行（H1）を検出
            if line.strip().startswith('# '):
                # H1をH2に変換（# を ## に）
                new_line = '#' + line.strip()
                new_lines.append(new_line)
                has_h1 = True
                if fixed_count == 0:  # 最初の1件だけ詳細を表示
                    print(f"  変換例: {line[:50]}... -> {new_line[:50]}...")
            else:
                new_lines.append(line)
        
        if has_h1:
            ed.contents = '\n'.join(new_lines)
            ed.save()
            print(f"  Fixed EventDetail ID {ed.id}: {ed.title[:30]}...")
            fixed_count += 1
    
    print(f"\nEventDetail: {fixed_count}件の本文内H1をH2に変換")
    
    print(f"\n=== 修正完了 ===")


if __name__ == '__main__':
    fix_inner_h1_tags()