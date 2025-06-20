#!/usr/bin/env python
"""データベースに登録されていないGoogleカレンダーイベントを削除"""

import os
import sys
import django

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from event.sync_to_google import DatabaseToGoogleSync

def cleanup_orphaned_events():
    """データベースに登録されていないイベントを削除"""
    print("=== 孤立したGoogleカレンダーイベントを削除 ===\n")
    
    sync = DatabaseToGoogleSync()
    
    # 削除対象のイベントID
    orphaned_ids = [
        '068ge7hi5fbj1nguod409oes86_20250621T130000Z',  # ITインフラ集会の重複
        '1aukd6h46vfk34rftlqhqv6j3v_20250622T110000Z',  # VRCHoudini勉強会の重複
        'tf5686kqgv59j7cucshsdqo174'                     # VRCHoudini勉強会の重複
    ]
    
    deleted_count = 0
    
    for event_id in orphaned_ids:
        try:
            sync.service.delete_event(event_id)
            deleted_count += 1
            print(f"✓ 削除完了: {event_id}")
        except Exception as e:
            print(f"✗ 削除エラー: {event_id} - {e}")
    
    print(f"\n✓ {deleted_count}件のイベントを削除しました")

if __name__ == '__main__':
    cleanup_orphaned_events()