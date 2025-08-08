import json
from datetime import date, time
from django.test import TestCase
from django.contrib.auth import get_user_model
from community.models import Community
from event.models import Event, RecurrenceRule
from event.recurrence_service import RecurrenceService
from unittest.mock import patch, MagicMock

User = get_user_model()


class TestRecurrenceRuleGeneration(TestCase):
    """定期ルール生成の週計算テスト"""
    
    def setUp(self):
        # テストユーザーとコミュニティを作成
        self.user = User.objects.create_user(
            user_name='testuser',
            email='test@example.com',
            password='testpass'
        )
        
        self.community = Community.objects.create(
            custom_user=self.user,
            name='XR開発者集会',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='毎月第4月曜',
            organizers='XR Developer',
            platform='PC'
        )
        
        # 過去のイベントを作成（第4月曜日のパターン）
        past_events = [
            Event(community=self.community, date=date(2024, 11, 25), start_time=time(22, 0)),  # 第4月曜
            Event(community=self.community, date=date(2024, 10, 28), start_time=time(22, 0)),  # 第4月曜
            Event(community=self.community, date=date(2024, 9, 23), start_time=time(22, 0)),   # 第4月曜
            Event(community=self.community, date=date(2024, 8, 26), start_time=time(22, 0)),   # 第4月曜
            Event(community=self.community, date=date(2024, 7, 22), start_time=time(22, 0)),   # 第4月曜
        ]
        Event.objects.bulk_create(past_events)
        
        self.service = RecurrenceService()
    
    def test_generate_monthly_fourth_monday_with_llm(self):
        """LLMを使用して毎月第4月曜の定期ルールを生成"""
        # RecurrenceRuleを作成
        rule = RecurrenceRule.objects.create(
            community=self.community,
            frequency='OTHER',
            custom_rule='毎月第4月曜',
            start_date=date(2024, 12, 1)
        )
        
        # LLMのモックレスポンス（第4月曜日の日付）
        mock_response = MagicMock()
        mock_response.text = """
        以下が生成した日付リストです：
        ["2024-12-23", "2025-01-27", "2025-02-24"]
        """
        
        # OpenRouter用のモックレスポンス
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = mock_response.text
        
        with patch.object(self.service.client.chat.completions, 'create', return_value=mock_completion):
            # 日付を生成
            dates = self.service.generate_dates(
                rule=rule,
                base_date=date(2024, 12, 1),
                base_time=time(22, 0),
                months=3,
                community=self.community
            )
            
            # 生成された日付を確認
            expected_dates = [
                date(2024, 12, 23),  # 第4月曜
                date(2025, 1, 27),   # 第4月曜
                date(2025, 2, 24),   # 第4月曜
            ]
            
            self.assertEqual(dates, expected_dates)
            
            # 全て月曜日であることを確認
            for d in dates:
                self.assertEqual(d.weekday(), 0, f"{d} は月曜日ではありません (weekday={d.weekday()})")
    
    def test_llm_prompt_for_monthly_pattern(self):
        """月次パターンのLLMプロンプトを確認"""
        rule = RecurrenceRule.objects.create(
            community=self.community,
            frequency='OTHER', 
            custom_rule='毎月第4月曜',
            start_date=date(2024, 12, 1)
        )
        
        # プロンプト生成をキャプチャ
        captured_prompt = None
        
        def capture_prompt(*args, **kwargs):
            nonlocal captured_prompt
            # メッセージからプロンプトを取得
            messages = kwargs.get('messages', [])
            for msg in messages:
                if msg.get('role') == 'user':
                    captured_prompt = msg.get('content', '')
            # モックレスポンスを返す
            mock_completion = MagicMock()
            mock_completion.choices = [MagicMock()]
            mock_completion.choices[0].message.content = '[\"2024-12-23\"]'
            return mock_completion
        
        with patch.object(self.service.client.chat.completions, 'create', side_effect=capture_prompt):
            self.service.generate_dates(
                rule=rule,
                base_date=date(2024, 12, 1),
                base_time=time(22, 0),
                months=1,
                community=self.community
            )
        
        # プロンプトの内容を確認
        self.assertIsNotNone(captured_prompt)
        print("\n=== Captured LLM Prompt ===")
        print(captured_prompt)
        print("=== End of Prompt ===\n")
        
        self.assertIn('毎月第4月曜', captured_prompt)
        self.assertIn('基準日: 2024-12-01 (日曜日)', captured_prompt)
        self.assertIn('過去の開催履歴（直近5回）:', captured_prompt)
        self.assertIn('2024-11-25 (月曜日)', captured_prompt)
        self.assertIn('主な開催曜日: 月曜日', captured_prompt)
        
        # 週の分析を確認 - 第4週または第5週が検出されているはず
        # 月の最終週は第5週として計算される場合がある
        week_pattern_found = '主な開催週: 第4週' in captured_prompt or '主な開催週: 第5週' in captured_prompt
        self.assertTrue(week_pattern_found, "週のパターンが正しく検出されていません")
    
    def test_monthly_by_week_rule_generation(self):
        """MONTHLY_BY_WEEK頻度での第N曜日生成テスト"""
        rule = RecurrenceRule.objects.create(
            community=self.community,
            frequency='MONTHLY_BY_WEEK',
            week_of_month=4,
            start_date=date(2024, 12, 23)  # 第4月曜日から開始
        )
        
        # 日付を生成
        dates = self.service.generate_dates(
            rule=rule,
            base_date=date(2024, 12, 1),
            base_time=time(22, 0),
            months=3
        )
        
        # 期待される日付（第4月曜日）
        expected_dates = [
            date(2024, 12, 23),  # 第4月曜
            date(2025, 1, 27),   # 第4月曜  
            date(2025, 2, 24),   # 第4月曜
        ]
        
        self.assertEqual(dates, expected_dates)
        
        # 全て月曜日であることを確認
        for d in dates:
            self.assertEqual(d.weekday(), 0)
            
            # 第4週であることを確認
            week_of_month = (d.day - 1) // 7 + 1
            self.assertIn(week_of_month, [4, 5], f"{d} is week {week_of_month}, not 4th week")
    
    def test_recurrence_preview_api_for_custom_rule(self):
        """RecurrencePreviewAPIでカスタムルールのプレビューをテスト"""
        from django.urls import reverse
        from rest_framework.test import APIClient
        
        client = APIClient()
        client.force_authenticate(user=self.user)
        
        # プレビューリクエスト
        response = client.post(
            '/api/v1/recurrence-preview/',
            {
                'frequency': 'OTHER',
                'custom_rule': '毎月第4月曜',
                'base_date': '2024-12-01',
                'base_time': '22:00',
                'months': 3
            },
            format='json'
        )
        
        self.assertEqual(response.status_code, 200)
        
        # モックを設定していないため、実際のLLMレスポンスに依存
        # ここではAPIが正常に動作することを確認
        self.assertIn('dates', response.data)
        self.assertIn('success', response.data)
        self.assertIn('count', response.data)