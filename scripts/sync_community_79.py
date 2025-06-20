#!/usr/bin/env python
"""コミュニティID 79のみをGoogleカレンダーに同期"""

import os
import sys
import django

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from event.sync_to_google import DatabaseToGoogleSync
from community.models import Community

def sync_community_79():
    """コミュニティID 79のみを同期"""
    
    try:
        # コミュニティを取得
        community = Community.objects.get(id=79)
        print(f"=== {community.name} をGoogleカレンダーに同期 ===\n")
        
        # 同期実行
        sync = DatabaseToGoogleSync()
        stats = sync.sync_community_events(community, months_ahead=3)
        
        print(f"\n同期完了:")
        print(f"  作成: {stats.get('created', 0)}件")
        print(f"  更新: {stats.get('updated', 0)}件")
        print(f"  削除: {stats.get('deleted', 0)}件")
        print(f"  エラー: {stats.get('errors', 0)}件")
        
    except Community.DoesNotExist:
        print(f"✗ コミュニティID 79が見つかりません")
    except Exception as e:
        print(f"✗ エラーが発生しました: {e}")

if __name__ == '__main__':
    sync_community_79()