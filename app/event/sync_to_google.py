#!/usr/bin/env python
"""データベースからGoogleカレンダーへの同期処理"""
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
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
    
    def sync_all_communities(self, months_ahead: int = 1):
        """すべてのコミュニティのイベントを同期"""
        communities = Community.objects.filter(status='approved', end_at__isnull=True)
        
        total_stats = {
            'created': 0,
            'updated': 0,
            'deleted': 0,
            'errors': 0,
            'skipped': 0,
            'duplicate_prevented': 0
        }
        
        # 同期期間を設定
        start_date = timezone.now().date()
        end_date = start_date + timedelta(days=months_ahead * 30)
        
        # 最初に全てのGoogleカレンダーイベントを取得
        logger.info("Fetching all Google Calendar events...")
        all_google_events = self.service.list_events(
            time_min=datetime.combine(start_date, datetime.min.time()),
            time_max=datetime.combine(end_date, datetime.max.time()),
            max_results=2500  # 十分な数のイベントを取得
        )
        
        # イベントを日時+サマリーでインデックス化
        google_events_by_datetime_summary = self._index_events_by_datetime_and_summary(all_google_events)
        google_events_by_id = {e['id']: e for e in all_google_events}
        
        logger.info(f"Found {len(all_google_events)} events in Google Calendar")
        
        # 各コミュニティのイベントを処理
        for community in communities:
            try:
                stats = self._sync_community_events(
                    community, 
                    start_date, 
                    end_date,
                    google_events_by_datetime_summary,
                    google_events_by_id
                )
                for key in total_stats:
                    total_stats[key] += stats[key]
            except Exception as e:
                logger.error(f"Failed to sync {community.name}: {e}")
                total_stats['errors'] += 1
        
        # DBに存在しないGoogleカレンダーイベントを削除
        orphaned_deleted = self.delete_orphaned_google_events(
            all_google_events, 
            start_date, 
            end_date
        )
        
        total_stats['deleted'] = orphaned_deleted
        
        if orphaned_deleted > 0:
            logger.info(f"DBに存在しないGoogleカレンダーイベントを{orphaned_deleted}件削除しました")
        
        logger.info(
            f"Total sync completed: "
            f"created={total_stats['created']}, "
            f"updated={total_stats['updated']}, "
            f"skipped={total_stats['skipped']}, "
            f"deleted={total_stats['deleted']}, "
            f"errors={total_stats['errors']}"
        )
        
        return total_stats
    
    def _sync_community_events(
        self, 
        community: Community, 
        start_date: datetime.date,
        end_date: datetime.date,
        google_events_by_datetime_summary: Dict[str, Dict],
        google_events_by_id: Dict[str, Dict]
    ):
        """特定のコミュニティのイベントを同期"""
        logger.info(f"Syncing events for {community.name}")
        
        # データベースのイベントを取得
        db_events = Event.objects.filter(
            community=community,
            date__gte=start_date,
            date__lte=end_date
        ).order_by('date', 'start_time')
        
        # 処理統計
        stats = {
            'created': 0,
            'updated': 0,
            'deleted': 0,
            'errors': 0,
            'skipped': 0,
            'duplicate_prevented': 0
        }
        
        # データベースのイベントを処理
        for event in db_events:
            try:
                result = self._process_event(
                    event, 
                    google_events_by_datetime_summary, 
                    google_events_by_id
                )
                
                if result['action'] == 'created':
                    stats['created'] += 1
                elif result['action'] == 'updated':
                    stats['updated'] += 1
                    # 重複防止による更新をカウント
                    if result.get('duplicate_prevented'):
                        stats['duplicate_prevented'] += 1
                elif result['action'] == 'skipped':
                    stats['skipped'] += 1
                    
            except Exception as e:
                logger.error(f"Error processing event {event}: {e}")
                stats['errors'] += 1
        
        logger.info(
            f"Sync completed for {community.name}: "
            f"created={stats['created']}, updated={stats['updated']}, "
            f"skipped={stats['skipped']}, deleted={stats['deleted']}, errors={stats['errors']}"
        )
        
        return stats
    
    def _process_event(
        self, 
        event: Event, 
        google_events_by_datetime_summary: Dict[str, Dict],
        google_events_by_id: Dict[str, Dict]
    ) -> Dict:
        """個別のイベントを処理"""
        # 日時とコミュニティ名でキーを生成
        dt_key = self._create_datetime_key(event.date, event.start_time)
        combined_key = f"{dt_key}|{event.community.name}"
        
        logger.info(f"[SYNC DEBUG] Processing event: {event}")
        logger.info(f"[SYNC DEBUG]   Date: {event.date}, Time: {event.start_time}")
        logger.info(f"[SYNC DEBUG]   Community: {event.community.name}")
        logger.info(f"[SYNC DEBUG]   Combined key: {combined_key}")
        logger.info(f"[SYNC DEBUG]   Current Google ID: {event.google_calendar_event_id}")
        
        # 1. まず日時+コミュニティ名でマッチング
        if combined_key in google_events_by_datetime_summary:
            google_event = google_events_by_datetime_summary[combined_key]
            logger.info(f"[SYNC DEBUG]   Found matching Google event by datetime+summary: {google_event['id']}")
            
            # Google Calendar IDが異なる場合は更新（重複防止のキーポイント）
            if event.google_calendar_event_id != google_event['id']:
                logger.info(
                    f"[SYNC DEBUG]   Updating Google Calendar ID for {event}: "
                    f"{event.google_calendar_event_id} -> {google_event['id']}"
                )
                event.google_calendar_event_id = google_event['id']
                event.save(update_fields=['google_calendar_event_id'])
                
                # IDを更新したので、イベント内容も更新
                logger.info(f"[SYNC DEBUG]   Updating Google event after ID change")
                self._update_google_event(event)
                return {'action': 'updated', 'google_id': google_event['id']}
            else:
                # IDが一致している場合は更新不要
                logger.info(f"[SYNC DEBUG]   Google event already in sync, skipping update")
                return {'action': 'skipped', 'google_id': google_event['id']}
        
        # 2. 日時で見つからない場合、Google Calendar IDで確認
        elif event.google_calendar_event_id and event.google_calendar_event_id in google_events_by_id:
            logger.info(f"[SYNC DEBUG]   Found Google event by ID: {event.google_calendar_event_id}")
            logger.info(f"[SYNC DEBUG]   Event time may have changed, updating...")
            # IDは存在するが日時が異なる（イベントの時間が変更された可能性）
            self._update_google_event(event)
            return {'action': 'updated', 'google_id': event.google_calendar_event_id}
        
        # 3. どちらでも見つからない場合は新規作成
        else:
            logger.info(f"[SYNC DEBUG]   No matching Google event found")
            logger.info(f"[SYNC DEBUG]   Keys checked in google_events_by_datetime_summary:")
            # デバッグ用：同じ日付のキーをログ出力
            for key in google_events_by_datetime_summary.keys():
                if event.date.isoformat() in key:
                    logger.info(f"[SYNC DEBUG]     - {key}")
            
            # 既存のGoogle Calendar IDをクリア（無効なIDの場合）
            if event.google_calendar_event_id:
                logger.info(f"[SYNC DEBUG]   Clearing invalid Google Calendar ID: {event.google_calendar_event_id}")
                event.google_calendar_event_id = None
                event.save(update_fields=['google_calendar_event_id'])
            
            logger.info(f"[SYNC DEBUG]   Creating new Google event")
            result = self._create_google_event(event)
            
            # 重複防止が機能した場合
            if result.get('duplicate_prevented'):
                return {'action': 'updated', 'google_id': event.google_calendar_event_id, 'duplicate_prevented': True}
            else:
                return {'action': 'created', 'google_id': event.google_calendar_event_id}
    
    def _index_events_by_datetime_and_summary(self, events: List[Dict]) -> Dict[str, Dict]:
        """イベントを日時+サマリーでインデックス化"""
        indexed = {}
        
        logger.info(f"[INDEX DEBUG] Indexing {len(events)} Google Calendar events")
        
        for event in events:
            start = event.get('start', {})
            summary = event.get('summary', '')
            
            # 日時を抽出
            if 'dateTime' in start:
                dt = datetime.fromisoformat(start['dateTime'].replace('Z', '+00:00'))
                # タイムゾーンを考慮してローカル時間に変換
                dt = dt.astimezone(timezone.get_current_timezone())
                dt_key = self._create_datetime_key(dt.date(), dt.time())
                # 日時+サマリーをキーとして使用
                combined_key = f"{dt_key}|{summary}"
                
                # 同じキーのイベントが複数ある場合の処理
                if combined_key in indexed:
                    # 重複がある場合は、より新しいイベントを保持
                    existing_event = indexed[combined_key]
                    existing_created = existing_event.get('created', '')
                    new_created = event.get('created', '')
                    
                    logger.warning(
                        f"[INDEX DEBUG] Duplicate event found for {combined_key}: "
                        f"existing={existing_event['id']} (created: {existing_created}), "
                        f"new={event['id']} (created: {new_created})"
                    )
                    
                    # より新しいイベントを保持（作成日時で判断）
                    if new_created > existing_created:
                        indexed[combined_key] = event
                        logger.info(f"[INDEX DEBUG] Keeping newer event: {event['id']}")
                    else:
                        logger.info(f"[INDEX DEBUG] Keeping existing event: {existing_event['id']}")
                else:
                    logger.debug(f"[INDEX DEBUG] Indexed: {combined_key} -> {event['id']}")
                    indexed[combined_key] = event
            elif 'date' in start:
                # 全日イベントの場合（今回は対象外だが念のため）
                date = datetime.strptime(start['date'], '%Y-%m-%d').date()
                dt_key = f"{date}T00:00:00"
                combined_key = f"{dt_key}|{summary}"
                indexed[combined_key] = event
        
        return indexed
    
    def _create_datetime_key(self, date: datetime.date, time: datetime.time) -> str:
        """日時からキーを生成"""
        return f"{date}T{time.strftime('%H:%M:%S')}"
    
    def _create_google_event(self, event: Event):
        """Googleカレンダーにイベントを作成"""
        # 重複チェック: 作成前に再度確認
        dt_key = self._create_datetime_key(event.date, event.start_time)
        combined_key = f"{dt_key}|{event.community.name}"
        
        # 最新のGoogle Calendarイベントを取得して再確認
        start_datetime = datetime.combine(event.date, event.start_time)
        end_datetime = start_datetime + timedelta(hours=1)  # 1時間の範囲で検索
        
        tz = timezone.get_current_timezone()
        start_datetime = timezone.make_aware(start_datetime, tz)
        end_datetime = timezone.make_aware(end_datetime, tz)
        
        # 同じ時間帯のイベントを検索
        existing_events = self.service.list_events(
            time_min=start_datetime - timedelta(minutes=1),
            time_max=start_datetime + timedelta(minutes=1),
            max_results=50
        )
        
        # 同じコミュニティ名のイベントがあるか確認
        for existing_event in existing_events:
            if existing_event.get('summary') == event.community.name:
                logger.warning(
                    f"[DUPLICATE PREVENTION] Found existing event during creation: "
                    f"{event.community.name} at {event.date} {event.start_time}"
                )
                # 既存のイベントIDを保存して更新処理に切り替え
                event.google_calendar_event_id = existing_event['id']
                event.save(update_fields=['google_calendar_event_id'])
                self._update_google_event(event)
                # 重複防止カウンターを返す
                return {'duplicate_prevented': True}
        
        # 説明文を生成
        description = self._generate_description(event)
        
        try:
            result = self.service.create_event(
                summary=event.community.name,
                start_time=start_datetime,
                end_time=start_datetime + timedelta(minutes=event.duration),
                description=description,
                recurrence=None
            )
            
            # Google Calendar IDを保存
            event.google_calendar_event_id = result['id']
            event.save(update_fields=['google_calendar_event_id'])
            
            logger.info(f"Created Google event for {event}: {result['id']}")
        except Exception as e:
            logger.error(f"Failed to create Google event for {event}: {e}")
            raise
        
        # 通常の作成成功
        return {'duplicate_prevented': False}
    
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
            logger.info(f"Updated Google event for {event}: {event.google_calendar_event_id}")
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
            lines.append(f"\n{event.community.description}")
        
        if event.community.group_url:
            lines.append(f"\nURL: {event.community.group_url}")
        
        return "\n".join(lines)
    
    def delete_orphaned_google_events(self, all_google_events: List[Dict], start_date: datetime.date, end_date: datetime.date) -> int:
        """DBに存在しないGoogleカレンダーイベントを削除
        
        Args:
            all_google_events: 取得済みのGoogleカレンダーイベントリスト
            start_date: 対象期間の開始日
            end_date: 対象期間の終了日
        
        Returns:
            削除したイベント数
        """
        logger.info("DBに存在しないGoogleカレンダーイベントの削除を開始")
        
        # 1. GoogleカレンダーのイベントIDセットを作成
        google_event_ids = {event['id'] for event in all_google_events}
        logger.info(f"Googleカレンダーのイベント総数: {len(google_event_ids)}")
        
        # 2. DBに登録されているGoogle Calendar IDのセットを取得
        db_google_ids = set(
            Event.objects.filter(
                date__gte=start_date,
                date__lte=end_date,
                google_calendar_event_id__isnull=False
            ).values_list('google_calendar_event_id', flat=True)
        )
        logger.info(f"DBに登録されているGoogle Calendar ID数: {len(db_google_ids)}")
        
        # 3. DBに存在しないイベントIDを特定
        orphaned_ids = google_event_ids - db_google_ids
        logger.info(f"削除対象のイベント数: {len(orphaned_ids)}")
        
        # 4. 削除処理
        deleted_count = 0
        for event_id in orphaned_ids:
            try:
                # 削除対象の詳細をログ出力
                event = next((e for e in all_google_events if e['id'] == event_id), None)
                if event:
                    start_str = event['start'].get('dateTime', event['start'].get('date'))
                    logger.warning(
                        f"Googleカレンダーから削除: {event['summary']} "
                        f"日時: {start_str} "
                        f"ID: {event_id}"
                    )
                    self.service.delete_event(event_id)
                    deleted_count += 1
                    logger.info(f"削除成功: {event_id}")
            except Exception as e:
                logger.error(f"イベント削除エラー ID: {event_id}, エラー: {e}")
        
        logger.info(f"削除完了: {deleted_count}件のイベントを削除しました")
        return deleted_count


def sync_database_to_google():
    """同期処理を実行"""
    sync = DatabaseToGoogleSync()
    return sync.sync_all_communities()