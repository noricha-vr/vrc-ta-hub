#!/usr/bin/env python
"""データベースからGoogleカレンダーへの同期実行"""

import os
import sys
import django
from datetime import datetime, timedelta

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.utils import timezone
from event.sync_to_google import sync_database_to_google


def main():
    """Googleカレンダーへの同期を実行"""
    
    print("=== Googleカレンダーへの同期 ===\n")
    print(f"現在時刻: {timezone.now()}")
    print(f"カレンダーID: {os.environ.get('GOOGLE_CALENDAR_ID', 'Not set')}\n")
    
    try:
        # 同期実行
        print("同期を開始します...")
        result = sync_database_to_google()
        
        print("\n=== 同期結果 ===")
        print(f"作成: {result.get('created', 0)}件")
        print(f"更新: {result.get('updated', 0)}件")
        print(f"削除: {result.get('deleted', 0)}件")
        print(f"エラー: {result.get('errors', 0)}件")
        
        if result.get('error_details'):
            print("\n=== エラー詳細 ===")
            for error in result['error_details']:
                print(f"- {error}")
        
        print("\n✓ 同期が完了しました")
        
    except Exception as e:
        print(f"\n✗ 同期中にエラーが発生しました: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == '__main__':
    exit(main())