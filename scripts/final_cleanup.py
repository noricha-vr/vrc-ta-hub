#!/usr/bin/env python
"""最後の重複を削除"""

import os
import sys
import django
from datetime import datetime, timedelta

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.utils import timezone
from event.models import Event
from event.sync_to_google import DatabaseToGoogleSync

def final_cleanup():
    """最後の重複を削除"""
    print("=== 最後の重複削除 ===\n")
    
    sync = DatabaseToGoogleSync()
    
    # ゲーム開発集会Ⅲの重複イベントを確認
    duplicate_ids = [
        '01b068nl4sm2kk4ajf1d3hqqro_20250622T120000Z',
        '48pivffo3ke83a3iv3fvo2ebfs'
    ]
    
    # データベースに登録されているか確認
    for event_id in duplicate_ids:
        db_event = Event.objects.filter(google_calendar_event_id=event_id).first()
        if db_event:
            print(f"✓ {event_id} - DB登録あり（保持）")
        else:
            print(f"✗ {event_id} - DB登録なし（削除対象）")
            try:
                sync.service.delete_event(event_id)
                print(f"  削除完了")
            except Exception as e:
                print(f"  削除エラー: {e}")
    
    print("\n処理完了")

if __name__ == '__main__':
    final_cleanup()