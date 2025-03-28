from django.test import TestCase
from django.utils import timezone
from datetime import datetime, timedelta
import json
import os
from unittest.mock import patch, MagicMock

from community.models import Community
from event.models import Event
from event.views import delete_outdated_events, register_calendar_events


class EventSyncTest(TestCase):
    """
    イベント同期のテストクラス

    このテストクラスが必要な理由:
    1. データ整合性の保証: GoogleカレンダーとDBのイベントデータの同期が
       正しく機能することを確認し、データの整合性を保証します。
    2. エッジケースの検証: タイムゾーンの違いや、カレンダーイベントの欠落など、
       実運用で発生しうる様々なケースを検証します。
    3. 機能の信頼性担保: イベント同期は本システムの重要な機能であり、
       その信頼性を自動テストにより継続的に確認する必要があります。
    """

    def setUp(self):
        """
        テスト用データのセットアップ

        このメソッドが必要な理由:
        1. テストの独立性確保: 各テストケースが独立して実行できるよう、
           テストに必要なデータを毎回クリーンな状態で用意します。
        2. テストデータの一貫性: 全てのテストケースで同じ初期データを使用することで、
           テスト結果の再現性と信頼性を確保します。
        3. コード重複の防止: 複数のテストケースで必要となる共通のセットアップ処理を
           一箇所にまとめることで、コードの重複を防ぎます。
        """
        # テスト用のコミュニティを作成
        self.community1 = Community.objects.create(
            name="個人開発集会",
            status="approved"
        )
        self.community2 = Community.objects.create(
            name="OSS集会（オープンソースソフトウェア集会）",
            status="approved"
        )
        self.community3 = Community.objects.create(
            name="分解技術集会",
            status="approved"
        )

        # テスト用のイベントを作成
        today = timezone.now().date()
        future_date = today + timedelta(days=30)
        
        # 21:00開始のイベント
        self.event1 = Event.objects.create(
            community=self.community1,
            date=future_date,
            start_time="21:00:00",
            duration=60,
            weekday=future_date.strftime("%a"),
            google_calendar_event_id="event1_id"
        )
        
        # 22:00開始のイベント
        self.event2 = Event.objects.create(
            community=self.community2,
            date=future_date,
            start_time="22:00:00",
            duration=90,
            weekday=future_date.strftime("%a"),
            google_calendar_event_id="event2_id"
        )
        
        # 22:30開始のイベント
        self.event3 = Event.objects.create(
            community=self.community3,
            date=future_date,
            start_time="22:30:00",
            duration=60,
            weekday=future_date.strftime("%a"),
            google_calendar_event_id="event3_id"
        )
        
        # テスト用の環境変数を設定
        os.environ['TESTING'] = 'true'

    def tearDown(self):
        """テスト終了後の後処理"""
        # 環境変数をクリア
        if 'TESTING' in os.environ:
            del os.environ['TESTING']

    def test_delete_outdated_events_with_matching_events(self):
        """
        カレンダーイベントとDBのイベントが完全に一致する場合のテスト

        このテストが必要な理由:
        1. 正常系の確認: イベントデータが正しく同期されている場合に、
           不要な削除が発生しないことを確認します。
        2. 基本機能の保証: イベント同期の最も基本的なケースが正しく
           動作することを確認し、システムの信頼性を担保します。
        """
        # テスト用のカレンダーイベントを作成（全てのイベントに対応するカレンダーイベントあり）
        today = timezone.now().date()
        future_date = today + timedelta(days=30)
        
        # タイムゾーン付きの日時を作成
        start_time1 = datetime.combine(future_date, datetime.strptime("21:00:00", "%H:%M:%S").time())
        start_time1 = timezone.make_aware(start_time1)
        
        start_time2 = datetime.combine(future_date, datetime.strptime("22:00:00", "%H:%M:%S").time())
        start_time2 = timezone.make_aware(start_time2)
        
        start_time3 = datetime.combine(future_date, datetime.strptime("22:30:00", "%H:%M:%S").time())
        start_time3 = timezone.make_aware(start_time3)
        
        # モック用のカレンダーイベントリストを作成
        calendar_events = [
            {
                'id': 'event1_id',
                'summary': '個人開発集会',
                'start': {'dateTime': start_time1.strftime('%Y-%m-%dT%H:%M:%S%z')},
                'end': {'dateTime': (start_time1 + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S%z')}
            },
            {
                'id': 'event2_id',
                'summary': 'OSS集会（オープンソースソフトウェア集会）',
                'start': {'dateTime': start_time2.strftime('%Y-%m-%dT%H:%M:%S%z')},
                'end': {'dateTime': (start_time2 + timedelta(minutes=90)).strftime('%Y-%m-%dT%H:%M:%S%z')}
            },
            {
                'id': 'event3_id',
                'summary': '分解技術集会',
                'start': {'dateTime': start_time3.strftime('%Y-%m-%dT%H:%M:%S%z')},
                'end': {'dateTime': (start_time3 + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S%z')}
            }
        ]
        
        # 同期処理を実行
        with patch('event.views.logger') as mock_logger:
            delete_outdated_events(calendar_events, today)
            
            # ログ出力を確認
            self.assertTrue(any('イベント一致確認' in call[0][0] for call in mock_logger.info.call_args_list))
            self.assertFalse(any('削除対象イベント' in call[0][0] for call in mock_logger.warning.call_args_list))
        
        # すべてのイベントが削除されずに残っていることを確認
        self.assertEqual(Event.objects.count(), 3)
        self.assertTrue(Event.objects.filter(id=self.event1.id).exists())
        self.assertTrue(Event.objects.filter(id=self.event2.id).exists())
        self.assertTrue(Event.objects.filter(id=self.event3.id).exists())

    def test_delete_outdated_events_with_timezone_difference(self):
        """
        カレンダーイベントのタイムゾーンが異なる場合でも正しく判定されるかのテスト

        このテストが必要な理由:
        1. タイムゾーン対応の検証: 異なるタイムゾーンのイベントでも
           正しく同期されることを確認し、グローバルな利用に対応します。
        2. 時刻比較の正確性: 秒単位の違いを無視し、時・分のみで
           正しく比較できることを確認します。
        """
        # テスト用のカレンダーイベントを作成（秒が異なるケース）
        today = timezone.now().date()
        future_date = today + timedelta(days=30)
        
        # タイムゾーン付きの日時を作成（秒を30に設定）
        start_time1 = datetime.combine(future_date, datetime.strptime("21:00:30", "%H:%M:%S").time())
        start_time1 = timezone.make_aware(start_time1)
        
        start_time2 = datetime.combine(future_date, datetime.strptime("22:00:45", "%H:%M:%S").time())
        start_time2 = timezone.make_aware(start_time2)
        
        start_time3 = datetime.combine(future_date, datetime.strptime("22:30:15", "%H:%M:%S").time())
        start_time3 = timezone.make_aware(start_time3)
        
        # モック用のカレンダーイベントリストを作成
        calendar_events = [
            {
                'id': 'event1_id',
                'summary': '個人開発集会',
                'start': {'dateTime': start_time1.strftime('%Y-%m-%dT%H:%M:%S%z')},
                'end': {'dateTime': (start_time1 + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S%z')}
            },
            {
                'id': 'event2_id',
                'summary': 'OSS集会（オープンソースソフトウェア集会）',
                'start': {'dateTime': start_time2.strftime('%Y-%m-%dT%H:%M:%S%z')},
                'end': {'dateTime': (start_time2 + timedelta(minutes=90)).strftime('%Y-%m-%dT%H:%M:%S%z')}
            },
            {
                'id': 'event3_id',
                'summary': '分解技術集会',
                'start': {'dateTime': start_time3.strftime('%Y-%m-%dT%H:%M:%S%z')},
                'end': {'dateTime': (start_time3 + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S%z')}
            }
        ]
        
        # 同期処理を実行
        with patch('event.views.logger') as mock_logger:
            delete_outdated_events(calendar_events, today)
        
        # すべてのイベントが削除されずに残っていることを確認（秒が異なっても時・分が一致していれば削除されない）
        self.assertEqual(Event.objects.count(), 3)
        self.assertTrue(Event.objects.filter(id=self.event1.id).exists())
        self.assertTrue(Event.objects.filter(id=self.event2.id).exists())
        self.assertTrue(Event.objects.filter(id=self.event3.id).exists())

    def test_delete_outdated_events_missing_calendar_events(self):
        """
        一部のイベントがカレンダーに存在しない場合のテスト

        このテストが必要な理由:
        1. データ整理の確認: カレンダーから削除されたイベントが
           DBからも適切に削除されることを確認します。
        2. 部分的な不一致への対応: 一部のイベントのみが不一致の場合でも、
           他のイベントには影響を与えずに正しく処理されることを確認します。
        """
        # テスト用のカレンダーイベントを作成（1つのイベントがカレンダーに存在しない）
        today = timezone.now().date()
        future_date = today + timedelta(days=30)
        
        # タイムゾーン付きの日時を作成
        start_time1 = datetime.combine(future_date, datetime.strptime("21:00:00", "%H:%M:%S").time())
        start_time1 = timezone.make_aware(start_time1)
        
        start_time3 = datetime.combine(future_date, datetime.strptime("22:30:00", "%H:%M:%S").time())
        start_time3 = timezone.make_aware(start_time3)
        
        # モック用のカレンダーイベントリスト（event2が存在しない）
        calendar_events = [
            {
                'id': 'event1_id',
                'summary': '個人開発集会',
                'start': {'dateTime': start_time1.strftime('%Y-%m-%dT%H:%M:%S%z')},
                'end': {'dateTime': (start_time1 + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S%z')}
            },
            {
                'id': 'event3_id',
                'summary': '分解技術集会',
                'start': {'dateTime': start_time3.strftime('%Y-%m-%dT%H:%M:%S%z')},
                'end': {'dateTime': (start_time3 + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S%z')}
            }
        ]
        
        # 同期処理を実行
        with patch('event.views.logger') as mock_logger:
            delete_outdated_events(calendar_events, today)
            
            # ログ出力を確認
            self.assertTrue(any('削除対象イベント' in call[0][0] and 'OSS集会' in call[0][0] for call in mock_logger.warning.call_args_list))
        
        # event2だけが削除されていることを確認
        self.assertEqual(Event.objects.count(), 2)
        self.assertTrue(Event.objects.filter(id=self.event1.id).exists())
        self.assertFalse(Event.objects.filter(id=self.event2.id).exists())
        self.assertTrue(Event.objects.filter(id=self.event3.id).exists())

    def test_register_calendar_events(self):
        """
        新しいカレンダーイベントが登録されるテスト

        このテストが必要な理由:
        1. イベント作成機能の検証: 新規イベントが正しくDBに
           登録されることを確認します。
        2. データ整合性の確認: 登録されたイベントが期待通りの
           データを持つことを確認します。
        3. ログ出力の確認: イベント登録時の適切なログ出力を
           確認し、システムの監視性を確保します。
        """
        # テスト用のカレンダーイベントを作成
        today = timezone.now().date()
        future_date = today + timedelta(days=30)
        new_future_date = today + timedelta(days=31)
        
        # 既存のイベントとは異なる日付のイベント
        start_time_new = datetime.combine(new_future_date, datetime.strptime("20:00:00", "%H:%M:%S").time())
        start_time_new = timezone.make_aware(start_time_new)
        
        # 新規カレンダーイベント
        calendar_events = [
            {
                'id': 'new_event_id',
                'summary': '個人開発集会',
                'start': {'dateTime': start_time_new.strftime('%Y-%m-%dT%H:%M:%S%z')},
                'end': {'dateTime': (start_time_new + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S%z')}
            }
        ]
        
        # 同期処理を実行
        with patch('event.views.logger') as mock_logger:
            register_calendar_events(calendar_events)
            
            # ログ出力を確認
            self.assertTrue(any('Event created' in call[0][0] for call in mock_logger.info.call_args_list))
        
        # 新しいイベントが作成されたことを確認
        self.assertEqual(Event.objects.count(), 4)  # 元の3件 + 新規1件
        self.assertTrue(Event.objects.filter(
            community=self.community1,
            date=new_future_date,
            start_time="20:00:00"
        ).exists())


