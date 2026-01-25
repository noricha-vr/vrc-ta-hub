"""RecurrenceRuleの削除機能に関するテスト"""
from datetime import date, time, timedelta
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model

from community.models import Community
from event.models import Event, RecurrenceRule
from user_account.models import APIKey

User = get_user_model()


class RecurrenceRuleDeletionTest(TestCase):
    """RecurrenceRuleの削除に関するテスト"""
    
    def setUp(self):
        """テスト用データの準備"""
        # ユーザー作成
        self.user = User.objects.create_user(
            user_name='Test User',
            email='test@example.com',
            password='testpass123'
        )
        self.superuser = User.objects.create_superuser(
            user_name='Admin User',
            email='admin@example.com',
            password='adminpass123'
        )
        
        # コミュニティ作成
        self.community = Community.objects.create(
            name='Test Community',
            custom_user=self.user,
            start_time=time(22, 0),
            duration=60,
            weekdays='MON',
            frequency='毎週',
            status='approved'
        )
        
        # RecurrenceRule作成
        self.rule = RecurrenceRule.objects.create(
            frequency='WEEKLY',
            interval=1,
            start_date=date(2024, 1, 1)
        )
        
        # 過去のイベント作成
        self.past_event = Event.objects.create(
            community=self.community,
            date=date(2024, 1, 1),
            start_time=time(22, 0),
            duration=60,
            recurrence_rule=self.rule,
            is_recurring_master=True
        )
        
        # 未来のイベント作成
        today = timezone.now().date()
        self.future_events = []
        for i in range(1, 4):
            future_date = today + timedelta(days=7 * i)
            event = Event.objects.create(
                community=self.community,
                date=future_date,
                start_time=time(22, 0),
                duration=60,
                recurring_master=self.past_event
            )
            self.future_events.append(event)
    
    def test_delete_future_events_method(self):
        """delete_future_eventsメソッドのテスト"""
        # 初期状態の確認
        self.assertEqual(Event.objects.count(), 4)  # 過去1件 + 未来3件
        
        # 未来のイベントを削除
        deleted_count = self.rule.delete_future_events()
        
        # 結果確認
        self.assertEqual(deleted_count, 3)  # 未来の3件が削除される
        self.assertEqual(Event.objects.count(), 1)  # 過去の1件のみ残る
        self.assertTrue(Event.objects.filter(id=self.past_event.id).exists())
        
        # 未来のイベントが削除されたことを確認
        for event in self.future_events:
            self.assertFalse(Event.objects.filter(id=event.id).exists())
    
    def test_delete_future_events_with_specific_date(self):
        """特定の日付以降のイベントを削除するテスト"""
        # 2週間後以降のイベントのみ削除
        today = timezone.now().date()
        delete_from = today + timedelta(days=14)
        
        deleted_count = self.rule.delete_future_events(delete_from)
        
        # 2週間後以降のイベント数を確認（2件削除されるはず）
        self.assertEqual(deleted_count, 2)
        self.assertEqual(Event.objects.count(), 2)  # 過去1件 + 1週間後の1件
    
    def test_recurrence_rule_delete_cascade(self):
        """RecurrenceRule削除時のカスケード削除テスト"""
        # RecurrenceRuleを削除（delete_future_events=True）
        self.rule.delete()
        
        # 未来のイベントが削除され、過去のイベントは残ることを確認
        self.assertEqual(Event.objects.count(), 1)
        self.assertTrue(Event.objects.filter(id=self.past_event.id).exists())
        self.assertFalse(RecurrenceRule.objects.filter(id=self.rule.id).exists())
    
    def test_recurrence_rule_delete_without_events(self):
        """イベント削除なしでRecurrenceRuleを削除するテスト"""
        # RecurrenceRuleを削除（delete_future_events=False）
        self.rule.delete(delete_future_events=False)
        
        # イベントは削除されないことを確認
        self.assertEqual(Event.objects.count(), 4)
        self.assertFalse(RecurrenceRule.objects.filter(id=self.rule.id).exists())


class RecurrenceRuleAdminTest(TestCase):
    """RecurrenceRule管理画面のテスト"""
    
    def setUp(self):
        """テスト用データの準備"""
        self.client = Client()
        self.superuser = User.objects.create_superuser(
            user_name='Admin User',
            email='admin@example.com',
            password='adminpass123'
        )
        
        # コミュニティとルールの作成
        self.community = Community.objects.create(
            name='Test Community',
            custom_user=self.superuser,
            start_time=time(22, 0),
            duration=60,
            weekdays='MON',
            frequency='毎週',
            status='approved'
        )
        
        self.rule = RecurrenceRule.objects.create(
            frequency='WEEKLY',
            interval=1
        )
        
        # イベント作成
        today = timezone.now().date()
        self.master_event = Event.objects.create(
            community=self.community,
            date=today + timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            recurrence_rule=self.rule,
            is_recurring_master=True
        )
    
    def test_admin_delete_future_events_view(self):
        """管理画面の未来のイベント削除ビューのテスト"""
        self.client.login(username='Admin User', password='adminpass123')
        
        # 削除確認ページにアクセス
        url = f'/admin/event/recurrencerule/{self.rule.pk}/delete_future_events/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '未来のイベントを削除')
        self.assertContains(response, self.community.name)
    
    def test_admin_delete_future_events_post(self):
        """管理画面で未来のイベントを削除するテスト"""
        self.client.login(username='Admin User', password='adminpass123')
        
        # 削除実行
        url = f'/admin/event/recurrencerule/{self.rule.pk}/delete_future_events/'
        response = self.client.post(url, {
            'delete_from_date': timezone.now().date().strftime('%Y-%m-%d'),
            'delete_rule': 'on'
        })
        
        # リダイレクトされることを確認
        self.assertEqual(response.status_code, 302)
        
        # イベントとルールが削除されたことを確認
        self.assertEqual(Event.objects.count(), 0)
        self.assertFalse(RecurrenceRule.objects.filter(id=self.rule.id).exists())


class RecurrenceRuleAPITest(TestCase):
    """RecurrenceRule APIのテスト"""
    
    def setUp(self):
        """テスト用データの準備"""
        self.client = Client()
        self.superuser = User.objects.create_superuser(
            user_name='Admin User',
            email='admin@example.com',
            password='adminpass123'
        )
        
        # APIキー作成
        self.api_key = APIKey.objects.create(
            user=self.superuser,
            name='Test API Key'
        )
        
        # コミュニティとルールの作成
        self.community = Community.objects.create(
            name='Test Community',
            custom_user=self.superuser,
            start_time=time(22, 0),
            duration=60,
            weekdays='MON',
            frequency='毎週',
            status='approved'
        )
        
        self.rule = RecurrenceRule.objects.create(
            frequency='WEEKLY',
            interval=1
        )
        
        # イベント作成
        today = timezone.now().date()
        self.event = Event.objects.create(
            community=self.community,
            date=today + timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            recurrence_rule=self.rule,
            is_recurring_master=True
        )
    
    def test_api_list_recurrence_rules(self):
        """API: RecurrenceRule一覧取得のテスト"""
        response = self.client.get(
            '/api/v1/recurrence-rules/',
            HTTP_AUTHORIZATION=f'Bearer {self.api_key.key}'
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]['id'], self.rule.id)
    
    def test_api_delete_future_events(self):
        """API: 未来のイベント削除のテスト"""
        url = f'/api/v1/recurrence-rules/{self.rule.id}/delete_future_events/'
        response = self.client.post(
            url,
            data={
                'delete_from_date': timezone.now().date().strftime('%Y-%m-%d'),
                'delete_rule': True
            },
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {self.api_key.key}'
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])
        self.assertEqual(response.json()['deleted_events_count'], 1)
        self.assertTrue(response.json()['rule_deleted'])
        
        # イベントとルールが削除されたことを確認
        self.assertEqual(Event.objects.count(), 0)
        self.assertFalse(RecurrenceRule.objects.filter(id=self.rule.id).exists())
    
    def test_api_delete_recurrence_rule(self):
        """API: RecurrenceRule削除のテスト"""
        url = f'/api/v1/recurrence-rules/{self.rule.id}/'
        response = self.client.delete(
            url,
            HTTP_AUTHORIZATION=f'Bearer {self.api_key.key}'
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])
        self.assertEqual(response.json()['deleted_events_count'], 1)
        
        # イベントとルールが削除されたことを確認
        self.assertEqual(Event.objects.count(), 0)
        self.assertFalse(RecurrenceRule.objects.filter(id=self.rule.id).exists())
    
    def test_api_permission_denied(self):
        """API: 権限なしユーザーのアクセステスト"""
        # 一般ユーザー作成
        normal_user = User.objects.create_user(
            user_name='Normal User',
            email='normal@example.com',
            password='normalpass123'
        )
        normal_api_key = APIKey.objects.create(
            user=normal_user,
            name='Normal User API Key'
        )
        
        # アクセステスト
        response = self.client.get(
            '/api/v1/recurrence-rules/',
            HTTP_AUTHORIZATION=f'Bearer {normal_api_key.key}'
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 0)  # 空のリストが返される