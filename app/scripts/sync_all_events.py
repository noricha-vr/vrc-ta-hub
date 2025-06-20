#!/usr/bin/env python
"""全コミュニティのイベントをGoogleカレンダーに同期"""

import os
import sys
import django

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.conf import settings
from event.sync_to_google import GoogleCalendarService
from community.models import Community
from event.models import Event
from datetime import datetime, timedelta


def sync_all():
    """全コミュニティのイベントを同期"""
    calendar_id = settings.GOOGLE_CALENDAR_ID
    service = GoogleCalendarService(calendar_id)
    
    # 同期期間の設定（1ヶ月先まで）
    today = datetime.now().date()
    end_date = today + timedelta(days=30)
    
    print(f'同期期間: {today} から {end_date} まで')
    print('='*60)
    
    # アクティブなコミュニティを取得
    communities = Community.objects.filter(is_active=True)
    
    for community in communities:
        print(f'\n{community.name} (ID: {community.id}) の同期開始...')
        
        # 期間内のイベントを取得
        events = Event.objects.filter(
            community=community,
            date__gte=today,
            date__lte=end_date
        ).exclude(is_recurring_master=True)
        
        sync_count = 0
        for event in events:
            try:
                google_event_id = service.create_or_update_event(event)
                if google_event_id:
                    event.google_calendar_event_id = google_event_id
                    event.save(update_fields=['google_calendar_event_id'])
                    sync_count += 1
            except Exception as e:
                print(f'  エラー: {event.date} のイベント同期失敗: {e}')
        
        print(f'  → {sync_count}件のイベントを同期しました')
    
    print('\n同期完了！')


if __name__ == '__main__':
    sync_all()