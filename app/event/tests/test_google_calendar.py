import os
from datetime import datetime, timedelta
from unittest import TestCase

from event.google_calendar import GoogleCalendarService


class TestGoogleCalendarService(TestCase):
    def setUp(self):
        """テストの前準備"""
        self.calendar_id = 'fbd1334d10a177831a23dfd723199ab4d02036ae31cbc04d6fc33f08ad93a3e7@group.calendar.google.com'
        self.credentials_path = os.getenv('GOOGLE_CALENDAR_CREDENTIALS', '/app/credentials.json')
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
