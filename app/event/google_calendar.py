from datetime import datetime
from typing import Optional, List, Dict, Any

from google.auth import default
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from website.settings import DEBUG


class GoogleCalendarService:
    """Googleカレンダーを操作するためのサービスクラス"""
    SCOPES = ['https://www.googleapis.com/auth/calendar']

    def __init__(self, calendar_id: str, credentials_path: str = None):
        """
        Args:
            calendar_id: 操作対象のカレンダーID
            credentials_path: サービスアカウントのクレデンシャルファイルのパス（開発環境用）
        """
        self.calendar_id = calendar_id

        # 本番環境（DEBUG=False）ではデフォルトの認証情報を使用
        if not DEBUG:
            credentials, _ = default(scopes=self.SCOPES)
        else:
            # 開発環境（DEBUG=True）ではcredentials.jsonを使用
            credentials = service_account.Credentials.from_service_account_file(
                credentials_path, scopes=self.SCOPES)

        self.service = build('calendar', 'v3', credentials=credentials)

    def _create_weekly_rrule(self, days: List[str], interval: int = 1) -> str:
        """週次の繰り返しルールを作成する

        Args:
            days: 繰り返す曜日のリスト（例: ['MO', 'WE', 'FR']）
            interval: 繰り返し間隔（1なら毎週、2なら隔週）

        Returns:
            RFC 5545形式の繰り返しルール
        """
        days_str = ','.join(days)
        return f'RRULE:FREQ=WEEKLY;INTERVAL={interval};BYDAY={days_str}'

    def _create_monthly_by_date_rrule(self, dates: List[int]) -> str:
        """月次の日付指定の繰り返しルールを作成する

        Args:
            dates: 繰り返す日付のリスト（例: [8, 18, 28]）

        Returns:
            RFC 5545形式の繰り返しルール
        """
        dates_str = ','.join(str(d) for d in dates)
        return f'RRULE:FREQ=MONTHLY;BYMONTHDAY={dates_str}'

    def _create_monthly_by_week_rrule(self, week_number: int, day: str) -> str:
        """月次の第N週X曜日の繰り返しルールを作成する

        Args:
            week_number: 何週目か（1-5, -1で最終週）
            day: 曜日（MO, TU, WE, TH, FR, SA, SU）

        Returns:
            RFC 5545形式の繰り返しルール
        """
        return f'RRULE:FREQ=MONTHLY;BYDAY={week_number}{day}'

    def create_event(self,
                     summary: str,
                     start_time: datetime,
                     end_time: datetime,
                     description: Optional[str] = None,
                     location: Optional[str] = None,
                     recurrence: Optional[List[str]] = None) -> Dict[str, Any]:
        """イベントを作成する

        Args:
            summary: イベントのタイトル
            start_time: 開始時刻
            end_time: 終了時刻
            description: イベントの説明
            location: 場所
            recurrence: 繰り返しルール（RFC 5545形式）のリスト

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
        if recurrence:
            event['recurrence'] = recurrence

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
            # タイムゾーン処理を修正
            time_min_str = None
            if time_min:
                # JSTのタイムスタンプをUTCに変換せずに、タイムゾーン情報を保持したまま送信
                time_min_str = time_min.isoformat()
                
            time_max_str = None
            if time_max:
                # JSTのタイムスタンプをUTCに変換せずに、タイムゾーン情報を保持したまま送信
                time_max_str = time_max.isoformat()
                
            # APIリクエストのログ
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"Google Calendar API呼び出し: timeMin={time_min_str}, timeMax={time_max_str}")
            
            events_result = self.service.events().list(
                calendarId=self.calendar_id,
                maxResults=max_results,
                timeMin=time_min_str,
                timeMax=time_max_str,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            logger.info(f"Google Calendar APIから{len(events)}件のイベントを取得")
            return events
        except HttpError as error:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f'Google Calendar API呼び出しエラー: {error}', exc_info=True)
            raise
