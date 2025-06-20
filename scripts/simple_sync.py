#!/usr/bin/env python
"""シンプルな同期実行"""

import os
import sys
import django
import time

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from event.sync_to_google import sync_database_to_google

def simple_sync():
    """シンプルな同期実行"""
    print("=== シンプルな同期を実行 ===\n")
    
    print("10秒待機してAPIレート制限をクリア...")
    time.sleep(10)
    
    print("\n同期を開始します...")
    sync_database_to_google()
    
    print("\n同期処理が開始されました")

if __name__ == '__main__':
    simple_sync()