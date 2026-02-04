from datetime import datetime, timedelta
import os
import unittest
from unittest import TestCase

import pytz
from django.utils import timezone

from event.google_calendar import GoogleCalendarService
from website.settings import GOOGLE_CALENDAR_ID, GOOGLE_CALENDAR_CREDENTIALS


RUN_EXTERNAL_API_TESTS = os.environ.get("RUN_EXTERNAL_API_TESTS") == "1"
CREDENTIALS_PATH = str(GOOGLE_CALENDAR_CREDENTIALS or "")
HAS_CREDENTIALS_FILE = bool(CREDENTIALS_PATH) and os.path.exists(CREDENTIALS_PATH)


@unittest.skipUnless(
    RUN_EXTERNAL_API_TESTS and HAS_CREDENTIALS_FILE,
    "外部APIテストのため RUN_EXTERNAL_API_TESTS=1 と GOOGLE_CALENDAR_CREDENTIALS ファイルが必要です",
)
class TestGoogleCalendarService(TestCase):
    def setUp(self):
        """テストの前準備"""
        self.calendar_id = GOOGLE_CALENDAR_ID
        self.credentials_path = GOOGLE_CALENDAR_CREDENTIALS

        self.service = GoogleCalendarService(self.calendar_id, self.credentials_path)

    def test_create_and_delete_event(self):
        """イベントの作成と削除のテスト"""
        # テスト用のイベントデータ
        summary = 'テストイベント'
        start_time = datetime.now() + timedelta(days=1)
        end_time = start_time + timedelta(hours=2)
        description = 'テストイベントの説明'
        location = 'バーチャル会場'

        try:
            # イベントを作成
            event = self.service.create_event(
                summary=summary,
                start_time=start_time,
                end_time=end_time,
                description=description,
                location=location
            )

            # 作成されたイベントの内容を確認
            self.assertEqual(event['summary'], summary)
            self.assertEqual(event['description'], description)
            self.assertEqual(event['location'], location)

            # イベントを削除
            self.service.delete_event(event['id'])

            # イベントが削除されたことを確認（イベントリストから削除されていることを確認）
            time_min = datetime.now()
            time_max = datetime.now() + timedelta(days=2)
            listed_events = self.service.list_events(
                max_results=10,
                time_min=time_min,
                time_max=time_max
            )
            event_ids = [e['id'] for e in listed_events]
            self.assertNotIn(event['id'], event_ids, "削除したイベントがまだリストに存在しています")

        except Exception as e:
            self.fail(f'テストが失敗しました: {str(e)}')

    def test_update_event(self):
        """イベントの更新のテスト"""
        # テスト用のイベントを作成
        summary = '更新前のイベント'
        start_time = datetime.now() + timedelta(days=1)
        end_time = start_time + timedelta(hours=2)

        try:
            # イベントを作成
            event = self.service.create_event(
                summary=summary,
                start_time=start_time,
                end_time=end_time
            )

            # イベントを更新
            new_summary = '更新後のイベント'
            new_start_time = start_time + timedelta(hours=1)
            new_end_time = end_time + timedelta(hours=1)

            updated_event = self.service.update_event(
                event_id=event['id'],
                summary=new_summary,
                start_time=new_start_time,
                end_time=new_end_time
            )

            # 更新された内容を確認
            self.assertEqual(updated_event['summary'], new_summary)

            # テスト後の後片付け
            self.service.delete_event(event['id'])

        except Exception as e:
            self.fail(f'テストが失敗しました: {str(e)}')

    def test_list_events(self):
        """イベント一覧取得のテスト"""
        # テスト用のイベントを作成
        events = []
        try:
            for i in range(3):
                start_time = datetime.now() + timedelta(days=i + 1)
                end_time = start_time + timedelta(hours=2)
                event = self.service.create_event(
                    summary=f'テストイベント{i + 1}',
                    start_time=start_time,
                    end_time=end_time
                )
                events.append(event)

            # イベント一覧を取得
            time_min = datetime.now()
            time_max = datetime.now() + timedelta(days=4)
            listed_events = self.service.list_events(
                max_results=10,
                time_min=time_min,
                time_max=time_max
            )

            # 取得したイベントの数を確認
            self.assertGreaterEqual(len(listed_events), len(events))

        except Exception as e:
            self.fail(f'テストが失敗しました: {str(e)}')
        finally:
            # テスト後の後片付け
            for event in events:
                try:
                    self.service.delete_event(event['id'])
                except Exception:
                    pass

    def test_create_weekly_recurring_event(self):
        """毎週月曜日の繰り返しイベントのテスト"""
        # テスト用のイベントデータ
        summary = '毎週月曜日のイベント'
        start_time = datetime.now() + timedelta(days=1)
        end_time = start_time + timedelta(hours=2)

        try:
            # 毎週月曜日の繰り返しルール
            recurrence = [self.service._create_weekly_rrule(['MO'])]

            # イベントを作成
            event = self.service.create_event(
                summary=summary,
                start_time=start_time,
                end_time=end_time,
                recurrence=recurrence
            )

            # 繰り返しルールが設定されていることを確認
            self.assertIn('recurrence', event)
            self.assertEqual(event['recurrence'], recurrence)

            # イベントを削除
            self.service.delete_event(event['id'])

        except Exception as e:
            self.fail(f'テストが失敗しました: {str(e)}')

    def test_create_biweekly_recurring_event(self):
        """隔週火曜日の繰り返しイベントのテスト"""
        summary = '隔週火曜日のイベント'
        start_time = datetime.now() + timedelta(days=1)
        end_time = start_time + timedelta(hours=2)

        try:
            # 隔週火曜日の繰り返しルール
            recurrence = [self.service._create_weekly_rrule(['TU'], interval=2)]

            event = self.service.create_event(
                summary=summary,
                start_time=start_time,
                end_time=end_time,
                recurrence=recurrence
            )

            self.assertIn('recurrence', event)
            self.assertEqual(event['recurrence'], recurrence)

            self.service.delete_event(event['id'])

        except Exception as e:
            self.fail(f'テストが失敗しました: {str(e)}')

    def test_create_monthly_recurring_event(self):
        """毎月第4土曜日の繰り返しイベントのテスト"""
        summary = '毎月第4土曜日のイベント'
        start_time = datetime.now() + timedelta(days=1)
        end_time = start_time + timedelta(hours=2)

        try:
            # 毎月第4土曜日の繰り返しルール
            recurrence = [self.service._create_monthly_by_week_rrule(4, 'SA')]

            event = self.service.create_event(
                summary=summary,
                start_time=start_time,
                end_time=end_time,
                recurrence=recurrence
            )

            self.assertIn('recurrence', event)
            self.assertEqual(event['recurrence'], recurrence)

            self.service.delete_event(event['id'])

        except Exception as e:
            self.fail(f'テストが失敗しました: {str(e)}')

    def test_create_monthly_by_date_recurring_event(self):
        """毎月8のつく日の繰り返しイベントのテスト"""
        summary = '毎月8のつく日のイベント'
        start_time = datetime.now() + timedelta(days=1)
        end_time = start_time + timedelta(hours=2)

        try:
            # 8のつく日（8日、18日、28日）の繰り返しルール
            recurrence = [self.service._create_monthly_by_date_rrule([8, 18, 28])]

            event = self.service.create_event(
                summary=summary,
                start_time=start_time,
                end_time=end_time,
                recurrence=recurrence
            )

            self.assertIn('recurrence', event)
            self.assertEqual(event['recurrence'], recurrence)

            self.service.delete_event(event['id'])

        except Exception as e:
            self.fail(f'テストが失敗しました: {str(e)}')

    def test_timezone_handling(self):
        """タイムゾーン処理のテスト、特に数時間後のイベント"""
        # テスト用のイベントデータ
        summary = 'タイムゾーンテストイベント'
        
        # 現在のタイムゾーンでの日時
        local_tz = timezone.get_current_timezone()
        now = timezone.now()
        
        # 数時間後のイベントを作成
        start_time = now + timedelta(hours=3)
        end_time = start_time + timedelta(hours=1)
        
        try:
            # イベントを作成
            event = self.service.create_event(
                summary=summary,
                start_time=start_time,
                end_time=end_time
            )
            
            # 作成されたイベントのIDを確認
            self.assertIsNotNone(event['id'])
            
            # イベント一覧を取得
            time_min = now
            time_max = now + timedelta(hours=24)
            listed_events = self.service.list_events(
                max_results=10,
                time_min=time_min,
                time_max=time_max
            )
            
            # 作成したイベントが存在するか確認
            created_event = next((e for e in listed_events if e['id'] == event['id']), None)
            self.assertIsNotNone(created_event, "作成したイベントがリストに存在しません")
            
            # イベントの開始時刻を確認（タイムゾーンの変換が正しいか）
            event_start = datetime.strptime(
                created_event['start'].get('dateTime'), 
                '%Y-%m-%dT%H:%M:%S%z'
            )
            
            # タイムゾーン変換後の時間が元の時間に近いことを確認
            event_start_local = event_start.astimezone(local_tz)
            time_diff = abs((event_start_local - start_time).total_seconds())
            self.assertLess(time_diff, 60, "タイムゾーン変換後の時間差が1分以上あります")
            
            # イベントを削除
            self.service.delete_event(event['id'])
        
        except Exception as e:
            self.fail(f'テストが失敗しました: {str(e)}')
