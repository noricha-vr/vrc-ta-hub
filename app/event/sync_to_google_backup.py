"""データベースからGoogleカレンダーへの同期処理"""
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
        
        # データベースのイベントを取得
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
        """Googleカレンダーにイベントを作成"""
        # イベントの開始・終了時刻を計算
        start_datetime = datetime.combine(event.date, event.start_time)
        end_datetime = start_datetime + timedelta(minutes=event.duration)
        
        # タイムゾーンを設定
        tz = timezone.get_current_timezone()
        start_datetime = timezone.make_aware(start_datetime, tz)
        end_datetime = timezone.make_aware(end_datetime, tz)
        
        # 定期イベントの場合、RRULEを設定
        recurrence = None
        if event.is_recurring_master and event.recurrence_rule:
            rrule = self._generate_rrule(event.recurrence_rule, event.date)
            if rrule:
                recurrence = [rrule]
        
        # 説明文を生成
        description = self._generate_description(event)
        
        try:
            result = self.service.create_event(
                summary=event.community.name,
                start_time=start_datetime,
                end_time=end_datetime,
                description=description,
                recurrence=recurrence
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
    
    def _prepare_event_data(self, event: Event) -> Dict:
        """イベントデータをGoogle Calendar API形式に変換"""
        start_datetime = datetime.combine(event.date, event.start_time)
        end_datetime = start_datetime + timedelta(minutes=event.duration)
        
        # タイムゾーンを設定
        tz = timezone.get_current_timezone()
        start_datetime = timezone.make_aware(start_datetime, tz)
        end_datetime = timezone.make_aware(end_datetime, tz)
        
        return {
            'summary': f'{event.community.name}',
            'start': {
                'dateTime': start_datetime.isoformat(),
                'timeZone': str(tz),
            },
            'end': {
                'dateTime': end_datetime.isoformat(),
                'timeZone': str(tz),
            },
            'description': self._generate_description(event),
        }
    
    def _generate_description(self, event: Event) -> str:
        """イベントの説明文を生成"""
        lines = [
            f"集会: {event.community.name}",
            f"開催日時: {event.date.strftime('%Y年%m月%d日')} {event.start_time.strftime('%H:%M')}",
            f"開催時間: {event.duration}分",
        ]
        
        if event.community.description:
            lines.append(f"\n{event.community.description}")
        
        if event.community.group_url:
            lines.append(f"\nURL: {event.community.group_url}")
        
        return "\n".join(lines)
    
    def _generate_rrule(self, rule: RecurrenceRule, start_date: datetime.date) -> Optional[str]:
        """RecurrenceRuleからRRULE文字列を生成"""
        if rule.frequency == 'WEEKLY':
            freq = 'WEEKLY'
            interval = rule.interval
            
            if rule.end_date:
                until = rule.end_date.strftime('%Y%m%d')
                return f'RRULE:FREQ={freq};INTERVAL={interval};UNTIL={until}'
            else:
                return f'RRULE:FREQ={freq};INTERVAL={interval}'
        
        elif rule.frequency == 'MONTHLY_BY_DATE':
            freq = 'MONTHLY'
            interval = rule.interval
            
            if rule.end_date:
                until = rule.end_date.strftime('%Y%m%d')
                return f'RRULE:FREQ={freq};INTERVAL={interval};UNTIL={until}'
            else:
                return f'RRULE:FREQ={freq};INTERVAL={interval}'
        
        elif rule.frequency == 'MONTHLY_BY_WEEK':
            freq = 'MONTHLY'
            interval = rule.interval
            
            # 曜日を取得
            weekday = start_date.strftime('%a').upper()[:2]
            
            # 第N週を設定
            if rule.week_of_month == -1:
                bysetpos = '-1'
            else:
                bysetpos = str(rule.week_of_month)
            
            if rule.end_date:
                until = rule.end_date.strftime('%Y%m%d')
                return f'RRULE:FREQ={freq};INTERVAL={interval};BYDAY={weekday};BYSETPOS={bysetpos};UNTIL={until}'
            else:
                return f'RRULE:FREQ={freq};INTERVAL={interval};BYDAY={weekday};BYSETPOS={bysetpos}'
        
        return None


def sync_database_to_google():
    """データベースからGoogleカレンダーへの同期を実行"""
    sync = DatabaseToGoogleSync()
    sync.sync_all_communities()