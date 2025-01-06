from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


class GoogleCalendarService:
    """Googleカレンダーを操作するためのサービスクラス"""
    SCOPES = ['https://www.googleapis.com/auth/calendar']
    
    def __init__(self, calendar_id: str, credentials_path: str):
        """
        Args:
            calendar_id: 操作対象のカレンダーID
            credentials_path: サービスアカウントのクレデンシャルファイルのパス
        """
        self.calendar_id = calendar_id
        credentials = service_account.Credentials.from_service_account_file(
            credentials_path, scopes=self.SCOPES)
        self.service = build('calendar', 'v3', credentials=credentials)

    def create_event(self, 
                    summary: str,
                    start_time: datetime,
                    end_time: datetime,
                    description: Optional[str] = None,
                    location: Optional[str] = None) -> Dict[str, Any]:
        """イベントを作成する

        Args:
            summary: イベントのタイトル
            start_time: 開始時刻
            end_time: 終了時刻
            description: イベントの説明
            location: 場所

        Returns:
            作成されたイベントの情報
        """
        event = {
            'summary': summary,
            'start': {
                'dateTime': start_time.isoformat(),
                'timeZone': 'Asia/Tokyo',
            },
            'end': {
                'dateTime': end_time.isoformat(),
                'timeZone': 'Asia/Tokyo',
            }
        }

        if description:
            event['description'] = description
        if location:
            event['location'] = location

        try:
            event = self.service.events().insert(
                calendarId=self.calendar_id,
                body=event
            ).execute()
            return event
        except HttpError as error:
            print(f'An error occurred: {error}')
            raise

    def update_event(self,
                    event_id: str,
                    summary: Optional[str] = None,
                    start_time: Optional[datetime] = None,
                    end_time: Optional[datetime] = None,
                    description: Optional[str] = None,
                    location: Optional[str] = None) -> Dict[str, Any]:
        """イベントを更新する

        Args:
            event_id: 更新対象のイベントID
            summary: 新しいタイトル
            start_time: 新しい開始時刻
            end_time: 新しい終了時刻
            description: 新しい説明
            location: 新しい場所

        Returns:
            更新されたイベントの情報
        """
        try:
            event = self.service.events().get(
                calendarId=self.calendar_id,
                eventId=event_id
            ).execute()

            if summary:
                event['summary'] = summary
            if start_time:
                event['start'] = {
                    'dateTime': start_time.isoformat(),
                    'timeZone': 'Asia/Tokyo'
                }
            if end_time:
                event['end'] = {
                    'dateTime': end_time.isoformat(),
                    'timeZone': 'Asia/Tokyo'
                }
            if description:
                event['description'] = description
            if location:
                event['location'] = location

            updated_event = self.service.events().update(
                calendarId=self.calendar_id,
                eventId=event_id,
                body=event
            ).execute()
            return updated_event
        except HttpError as error:
            print(f'An error occurred: {error}')
            raise

    def delete_event(self, event_id: str) -> None:
        """イベントを削除する

        Args:
            event_id: 削除対象のイベントID
        """
        try:
            self.service.events().delete(
                calendarId=self.calendar_id,
                eventId=event_id
            ).execute()
        except HttpError as error:
            print(f'An error occurred: {error}')
            raise

    def list_events(self,
                   max_results: int = 10,
                   time_min: Optional[datetime] = None,
                   time_max: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """イベントの一覧を取得する

        Args:
            max_results: 取得する最大件数
            time_min: この時刻以降のイベントを取得
            time_max: この時刻以前のイベントを取得

        Returns:
            イベントのリスト
        """
        try:
            events_result = self.service.events().list(
                calendarId=self.calendar_id,
                maxResults=max_results,
                timeMin=time_min.isoformat() + 'Z' if time_min else None,
                timeMax=time_max.isoformat() + 'Z' if time_max else None,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            return events_result.get('items', [])
        except HttpError as error:
            print(f'An error occurred: {error}')
            raise 
