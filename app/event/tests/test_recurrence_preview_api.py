import json
from datetime import date, time
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient
from community.models import Community
from event.models import Event, RecurrenceRule

User = get_user_model()


class TestRecurrencePreviewAPI(TestCase):
    """定期ルールプレビューAPIのテスト"""
    
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
        ]
        Event.objects.bulk_create(past_events)
        
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
    
    def test_recurrence_preview_with_custom_rule(self):
        """カスタムルールでのプレビューAPI"""
        response = self.client.post(
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
        
        print("\n=== API Response ===")
        print(json.dumps(response.data, indent=2, ensure_ascii=False))
        
        # レスポンスの検証
        self.assertIn('dates', response.data)
        self.assertIn('success', response.data)
        self.assertTrue(response.data['success'])
        
        # 生成された日付を確認
        dates = response.data['dates']
        self.assertGreater(len(dates), 0, "日付が生成されていません")
        
        # 各日付が月曜日であることを確認
        for date_str in dates:
            d = date.fromisoformat(date_str)
            # このテストケースでは月曜日を期待
            self.assertEqual(d.weekday(), 0, f"{date_str} は月曜日ではありません")
    
    def test_recurrence_preview_with_monthly_by_week(self):
        """MONTHLY_BY_WEEK頻度でのプレビューAPI"""
        response = self.client.post(
            '/api/v1/recurrence-preview/',
            {
                'frequency': 'MONTHLY_BY_WEEK',
                'week_of_month': 4,
                'weekday': 0,  # 月曜日
                'base_date': '2024-12-01',
                'base_time': '22:00',
                'months': 3
            },
            format='json'
        )
        
        print("\n=== MONTHLY_BY_WEEK API Response ===")
        print(f"Status: {response.status_code}")
        print(f"Response: {response.data}")
        
        self.assertEqual(response.status_code, 200)
        print(json.dumps(response.data, indent=2, ensure_ascii=False))
        
        # レスポンスの検証
        self.assertIn('dates', response.data)
        self.assertIn('success', response.data)
        self.assertTrue(response.data['success'])
        
        # 生成された日付を確認
        dates = response.data['dates']
        expected_dates = [
            '2024-12-23',  # 第4月曜
            '2025-01-27',  # 第4月曜
            '2025-02-24',  # 第4月曜
        ]
        
        # 第4月曜日が正しく生成されることを確認
        self.assertEqual(dates, expected_dates, f"期待される日付と異なります: {dates}")