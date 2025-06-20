#!/usr/bin/env python
"""同期ロジックを修正して重複を防ぐ"""

import os
import sys
import django

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from event.sync_to_google import DatabaseToGoogleSync

# sync_to_google.pyのパッチを作成
patch_content = '''#!/usr/bin/env python
"""データベースからGoogleカレンダーへの同期処理（修正版）"""
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from event.models import Event, RecurrenceRule
from event.google_calendar import GoogleCalendarService
from community.models import Community


logger = logging.getLogger(__name__)


class DatabaseToGoogleSync:
    """データベースを主体としたGoogleカレンダー同期"""
    
    def __init__(self):
        self.calendar_id = settings.GOOGLE_CALENDAR_ID
        self.service = GoogleCalendarService(
            calendar_id=self.calendar_id,
            credentials_path=settings.GOOGLE_CALENDAR_CREDENTIALS
        )
    
    def sync_all_communities(self, months_ahead: int = 3):
        """すべてのコミュニティのイベントを同期"""
        communities = Community.objects.filter(status='approved')
        
        for community in communities:
            try:
                self.sync_community_events(community, months_ahead)
            except Exception as e:
                logger.error(f"Failed to sync {community.name}: {e}")
    
    def sync_community_events(self, community: Community, months_ahead: int = 3):
        """特定のコミュニティのイベントを同期"""
        logger.info(f"Syncing events for {community.name}")
        
        # 同期期間を設定
        start_date = timezone.now().date()
        end_date = start_date + timedelta(days=months_ahead * 30)
        
        # データベースのイベントを取得（個別イベントのみ）
        db_events = Event.objects.filter(
            community=community,
            date__gte=start_date,
            date__lte=end_date
        ).order_by('date', 'start_time')
        
        # Googleカレンダーのイベントを取得
        google_events = self._get_google_events(community, start_date, end_date)
        google_events_dict = {e['id']: e for e in google_events}
        
        # 処理統計
        stats = {
            'created': 0,
            'updated': 0,
            'deleted': 0,
            'errors': 0
        }
        
        # データベースのイベントを処理
        processed_google_ids = set()
        
        for event in db_events:
            try:
                if event.google_calendar_event_id:
                    # 既存のGoogleイベントを更新
                    if event.google_calendar_event_id in google_events_dict:
                        self._update_google_event(event)
                        processed_google_ids.add(event.google_calendar_event_id)
                        stats['updated'] += 1
                    else:
                        # GoogleカレンダーにないのでIDをクリアして新規作成
                        event.google_calendar_event_id = None
                        event.save()
                        self._create_google_event(event)
                        processed_google_ids.add(event.google_calendar_event_id)
                        stats['created'] += 1
                else:
                    # 新規作成
                    self._create_google_event(event)
                    if event.google_calendar_event_id:
                        processed_google_ids.add(event.google_calendar_event_id)
                        stats['created'] += 1
            except Exception as e:
                logger.error(f"Error processing event {event}: {e}")
                stats['errors'] += 1
        
        # Googleカレンダーの余分なイベントを削除
        # 安全のため、削除処理をスキップするオプション
        delete_orphaned_events = getattr(settings, 'DELETE_ORPHANED_GOOGLE_EVENTS', False)
        
        if delete_orphaned_events:
            for google_id, google_event in google_events_dict.items():
                if google_id not in processed_google_ids:
                    try:
                        self.service.delete_event(google_id)
                        stats['deleted'] += 1
                    except Exception as e:
                        logger.error(f"Error deleting Google event {google_id}: {e}")
                        stats['errors'] += 1
        else:
            # 削除せずにログのみ
            orphaned_count = len([gid for gid in google_events_dict if gid not in processed_google_ids])
            if orphaned_count > 0:
                logger.info(f"Found {orphaned_count} orphaned events in Google Calendar (not deleted)")
        
        logger.info(
            f"Sync completed for {community.name}: "
            f"created={stats['created']}, updated={stats['updated']}, "
            f"deleted={stats['deleted']}, errors={stats['errors']}"
        )
        
        return stats
    
    def _get_google_events(self, community: Community, start_date: datetime.date, end_date: datetime.date) -> List[Dict]:
        """Googleカレンダーから特定コミュニティのイベントを取得"""
        all_events = self.service.list_events(
            time_min=datetime.combine(start_date, datetime.min.time()),
            time_max=datetime.combine(end_date, datetime.max.time())
        )
        
        # コミュニティ名でフィルタリング
        community_events = []
        for event in all_events:
            summary = event.get('summary', '')
            if community.name in summary:
                community_events.append(event)
        
        return community_events
    
    def _create_google_event(self, event: Event):
        """Googleカレンダーにイベントを作成（繰り返しルールなし）"""
        # イベントの開始・終了時刻を計算
        start_datetime = datetime.combine(event.date, event.start_time)
        end_datetime = start_datetime + timedelta(minutes=event.duration)
        
        # タイムゾーンを設定
        tz = timezone.get_current_timezone()
        start_datetime = timezone.make_aware(start_datetime, tz)
        end_datetime = timezone.make_aware(end_datetime, tz)
        
        # 説明文を生成
        description = self._generate_description(event)
        
        try:
            # 繰り返しルールは設定しない（個別イベントとして作成）
            result = self.service.create_event(
                summary=event.community.name,
                start_time=start_datetime,
                end_time=end_datetime,
                description=description,
                recurrence=None  # 繰り返しルールを無効化
            )
            event.google_calendar_event_id = result['id']
            event.save()
        except Exception as e:
            logger.error(f"Failed to create Google event for {event}: {e}")
            raise
    
    def _update_google_event(self, event: Event):
        """Googleカレンダーのイベントを更新"""
        # イベントの開始・終了時刻を計算
        start_datetime = datetime.combine(event.date, event.start_time)
        end_datetime = start_datetime + timedelta(minutes=event.duration)
        
        # タイムゾーンを設定
        tz = timezone.get_current_timezone()
        start_datetime = timezone.make_aware(start_datetime, tz)
        end_datetime = timezone.make_aware(end_datetime, tz)
        
        # 説明文を生成
        description = self._generate_description(event)
        
        try:
            self.service.update_event(
                event_id=event.google_calendar_event_id,
                summary=event.community.name,
                start_time=start_datetime,
                end_time=end_datetime,
                description=description
            )
        except Exception as e:
            logger.error(f"Failed to update Google event for {event}: {e}")
            raise
    
    def _generate_description(self, event: Event) -> str:
        """イベントの説明文を生成"""
        lines = [
            f"集会: {event.community.name}",
            f"開催日時: {event.date.strftime('%Y年%m月%d日')} {event.start_time.strftime('%H:%M')}",
            f"開催時間: {event.duration}分",
        ]
        
        if event.community.description:
            lines.append(f"\\n{event.community.description}")
        
        if event.community.group_url:
            lines.append(f"\\nURL: {event.community.group_url}")
        
        return "\\n".join(lines)


def sync_database_to_google():
    """データベースからGoogleカレンダーへの同期を実行"""
    sync = DatabaseToGoogleSync()
    sync.sync_all_communities()
'''

# ファイルに書き込み
with open('/opt/project/app/event/sync_to_google_fixed.py', 'w') as f:
    f.write(patch_content)

print("修正版の同期ロジックを作成しました: /opt/project/app/event/sync_to_google_fixed.py")
print("\n主な変更点:")
print("1. 繰り返しルール（recurrence）を無効化")
print("2. 個別イベントのみを同期")
print("3. is_recurring_masterフラグを無視")
print("\nこれにより、Googleカレンダー側での自動的な繰り返しイベント生成を防ぎ、")
print("データベースの個別イベントと1対1で対応するようになります。")

if __name__ == '__main__':
    pass