import json
import os
from datetime import date, time
from django.test import TestCase
from django.contrib.auth import get_user_model
from community.models import Community
from event.models import Event, RecurrenceRule
from event.recurrence_service import RecurrenceService

User = get_user_model()


class TestRecurrenceLLMGeneration(TestCase):
    """実際のLLMを使用した定期ルール生成テスト"""
    
    def setUp(self):
        # テストユーザーとコミュニティを作成
        self.user = User.objects.create_user(
            user_name='testuser',
            email='test@example.com',
            password='testpass'
        )
        
        self.community = Community.objects.create(
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
    
    def test_real_llm_generation_for_monthly_pattern(self):
        """実際のLLMを使用して月次パターンの定期ルールを生成"""
        # APIキーが設定されていない場合はスキップ
        if not os.environ.get('GOOGLE_API_KEY'):
            self.skipTest("GOOGLE_API_KEY not set")
        
        # RecurrenceRuleを作成
        rule = RecurrenceRule.objects.create(
            community=self.community,
            frequency='OTHER',
            custom_rule='毎月第4月曜',
            start_date=date(2024, 12, 1)
        )
        
        # 実際のLLMで日付を生成（2025年3月まで）
        dates = self.service.generate_dates(
            rule=rule,
            base_date=date(2024, 12, 1),
            base_time=time(22, 0),
            months=4,  # 2025年3月まで
            community=self.community
        )
        
        print("\n=== 実際のLLMが生成した日付 ===")
        for d in dates:
            weekday_jp = self.service._get_japanese_weekday(d.weekday())
            week_num = self.service._get_week_of_month(d)
            print(f"{d} ({weekday_jp}) - 第{week_num}週")
        
        # 期待される第4月曜日
        expected_dates = [
            date(2024, 12, 23),  # 第4月曜
            date(2025, 1, 27),   # 第4月曜
            date(2025, 2, 24),   # 第4月曜
            date(2025, 3, 24),   # 第4月曜
        ]
        
        # 生成された日付が第4月曜日であることを確認
        for d in dates:
            # 月曜日であることを確認
            self.assertEqual(d.weekday(), 0, f"{d} は月曜日ではありません (weekday={d.weekday()})")
            
            # 第4週（その曜日の4回目）であることを確認
            week_of_month = self.service._get_week_of_month(d)
            self.assertEqual(week_of_month, 4, f"{d} は第{week_of_month}週ですが、第4週であるべきです")
        
        # 期待される日付と一致することを確認
        # LLMの結果によって異なる可能性があるため、完全一致ではなく妥当性をチェック
        print(f"\n期待される日付: {expected_dates}")
        print(f"生成された日付: {dates}")
        
        # 生成された日付が期待されるパターンに一致するか確認
        matching_dates = [d for d in dates if d in expected_dates]
        self.assertGreater(len(matching_dates), 0, "生成された日付に期待されるパターンが含まれていません")