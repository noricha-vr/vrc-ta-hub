from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient
from datetime import date, time

from user_account.models import CustomUser, APIKey
from community.models import Community
from event.models import Event, EventDetail


class EventDetailAPITest(TestCase):
    def setUp(self):
        """テストデータのセットアップ"""
        # テストユーザー作成
        self.user1 = CustomUser.objects.create_user(
            user_name='test_user1',
            email='test1@example.com',
            password='testpass123'
        )
        self.user2 = CustomUser.objects.create_user(
            user_name='test_user2',
            email='test2@example.com',
            password='testpass123'
        )
        self.superuser = CustomUser.objects.create_superuser(
            user_name='admin',
            email='admin@example.com',
            password='adminpass123'
        )
        
        # APIキー作成
        self.api_key1 = APIKey.objects.create(
            user=self.user1,
            name='Test API Key 1'
        )
        self.api_key2 = APIKey.objects.create(
            user=self.user2,
            name='Test API Key 2'
        )
        
        # コミュニティ作成
        from community.models import CommunityMember

        self.community1 = Community.objects.create(
            name='Test Community 1',
            start_time=time(20, 0),
            duration=120,
            weekdays='mon',
            frequency='weekly',
            organizers='Test Organizer 1',
            status='approved'
        )
        CommunityMember.objects.create(
            community=self.community1,
            user=self.user1,
            role=CommunityMember.Role.OWNER
        )

        self.community2 = Community.objects.create(
            name='Test Community 2',
            start_time=time(21, 0),
            duration=90,
            weekdays='tue',
            frequency='weekly',
            organizers='Test Organizer 2',
            status='approved'
        )
        CommunityMember.objects.create(
            community=self.community2,
            user=self.user2,
            role=CommunityMember.Role.OWNER
        )
        
        # イベント作成
        self.event1 = Event.objects.create(
            community=self.community1,
            date=date(2024, 12, 25),
            start_time=time(20, 0),
            duration=120,
            weekday='wed'
        )
        self.event2 = Event.objects.create(
            community=self.community2,
            date=date(2024, 12, 26),
            start_time=time(21, 0),
            duration=90,
            weekday='thu'
        )
        
        # イベント詳細作成
        self.event_detail1 = EventDetail.objects.create(
            event=self.event1,
            detail_type='LT',
            start_time=time(20, 0),
            duration=30,
            speaker='Speaker 1',
            theme='Test Theme 1',
            h1='Test Title 1',
            contents='Test contents 1'
        )
        
        self.client = APIClient()
        self.url = reverse('event-detail-api-list')
    
    def test_api_key_authentication(self):
        """APIキー認証のテスト"""
        # 認証なし
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        
        # 有効なAPIキー
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.api_key1.key}')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # APIキーヘッダー
        self.client.credentials(HTTP_X_API_KEY=self.api_key1.key)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # 無効なAPIキー
        self.client.credentials(HTTP_AUTHORIZATION='Bearer invalid_key')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
    
    def test_list_event_details(self):
        """イベント詳細一覧取得のテスト"""
        # ユーザー1は自分のイベント詳細のみ取得
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.api_key1.key}')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # ページネーションがある場合とない場合の両方に対応
        if 'results' in response.data:
            self.assertEqual(len(response.data['results']), 1)
            self.assertEqual(response.data['results'][0]['id'], self.event_detail1.id)
        else:
            self.assertEqual(len(response.data), 1)
            self.assertEqual(response.data[0]['id'], self.event_detail1.id)
        
        # Superuserは全て取得
        self.client.force_authenticate(user=self.superuser)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        if 'results' in response.data:
            self.assertEqual(len(response.data['results']), 1)
        else:
            self.assertEqual(len(response.data), 1)
    
    def test_create_event_detail(self):
        """イベント詳細作成のテスト"""
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.api_key1.key}')
        
        data = {
            'event': self.event1.id,
            'detail_type': 'LT',
            'start_time': '20:30:00',
            'duration': 20,
            'speaker': 'New Speaker',
            'theme': 'New Theme',
            'h1': 'New Title',
            'contents': 'New contents',
            'generate_from_pdf': False
        }
        
        response = self.client.post(self.url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(EventDetail.objects.count(), 2)
        
        # 別ユーザーのイベントには作成できない
        data['event'] = self.event2.id
        response = self.client.post(self.url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
    
    def test_update_event_detail(self):
        """イベント詳細更新のテスト"""
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.api_key1.key}')
        
        url = reverse('event-detail-api-detail', kwargs={'pk': self.event_detail1.id})
        data = {
            'event': self.event1.id,
            'detail_type': 'SPECIAL',
            'start_time': '20:00:00',
            'duration': 45,
            'speaker': 'Updated Speaker',
            'theme': 'Updated Theme',
            'h1': 'Updated Title',
            'contents': 'Updated contents'
        }
        
        response = self.client.put(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        self.event_detail1.refresh_from_db()
        self.assertEqual(self.event_detail1.detail_type, 'SPECIAL')
        self.assertEqual(self.event_detail1.speaker, 'Updated Speaker')
    
    def test_delete_event_detail(self):
        """イベント詳細削除のテスト"""
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.api_key1.key}')
        
        url = reverse('event-detail-api-detail', kwargs={'pk': self.event_detail1.id})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(EventDetail.objects.count(), 0)
    
    def test_permission_check(self):
        """権限チェックのテスト"""
        # ユーザー2が別ユーザーのイベント詳細を操作しようとする
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.api_key2.key}')
        
        # 読み取り（自分のもののみ表示）
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        if 'results' in response.data:
            self.assertEqual(len(response.data['results']), 0)
        else:
            self.assertEqual(len(response.data), 0)
        
        # 更新（権限エラー）
        url = reverse('event-detail-api-detail', kwargs={'pk': self.event_detail1.id})
        response = self.client.put(url, {'theme': 'Hacked'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        
        # 削除（権限エラー）
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
    
    def test_my_events_endpoint(self):
        """自分のイベント一覧エンドポイントのテスト"""
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.api_key1.key}')
        
        url = reverse('event-detail-api-my-events')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        if 'results' in response.data:
            self.assertEqual(len(response.data['results']), 1)
            self.assertEqual(response.data['results'][0]['id'], self.event_detail1.id)
        else:
            self.assertEqual(len(response.data), 1)
            self.assertEqual(response.data[0]['id'], self.event_detail1.id)
    
    def test_api_key_last_used_update(self):
        """APIキー最終使用時刻更新のテスト"""
        initial_last_used = self.api_key1.last_used
        
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.api_key1.key}')
        self.client.get(self.url)
        
        self.api_key1.refresh_from_db()
        self.assertIsNotNone(self.api_key1.last_used)
        if initial_last_used:
            self.assertGreater(self.api_key1.last_used, initial_last_used)
    
    def test_inactive_api_key(self):
        """無効化されたAPIキーのテスト"""
        self.api_key1.is_active = False
        self.api_key1.save()
        
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.api_key1.key}')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
    
    def test_pdf_generation_flag(self):
        """PDF自動生成フラグのテスト"""
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.api_key1.key}')
        
        data = {
            'event': self.event1.id,
            'detail_type': 'BLOG',
            'start_time': '21:00:00',
            'duration': 30,
            'speaker': 'PDF Speaker',
            'theme': 'PDF Theme',
            'generate_from_pdf': True
        }
        
        response = self.client.post(self.url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        # PDF処理の実装後、ここで処理結果を確認
    
    def test_session_authentication(self):
        """セッション認証のテスト"""
        self.client.force_authenticate(user=self.user1)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        if 'results' in response.data:
            self.assertEqual(len(response.data['results']), 1)
        else:
            self.assertEqual(len(response.data), 1)

    def test_additional_info_in_response(self):
        """additional_infoフィールドがレスポンスに含まれるテスト"""
        # additional_infoを設定
        self.event_detail1.additional_info = 'Test additional information'
        self.event_detail1.save()

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.api_key1.key}')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # レスポンスデータを取得
        if 'results' in response.data:
            data = response.data['results'][0]
        else:
            data = response.data[0]

        # additional_infoが含まれていることを確認
        self.assertIn('additional_info', data)
        self.assertEqual(data['additional_info'], 'Test additional information')

    def test_create_event_detail_with_additional_info(self):
        """additional_infoを含むイベント詳細作成のテスト"""
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.api_key1.key}')

        data = {
            'event': self.event1.id,
            'detail_type': 'LT',
            'start_time': '20:45:00',
            'duration': 15,
            'speaker': 'Additional Info Speaker',
            'theme': 'Additional Info Theme',
            'additional_info': 'This is my additional information for the LT'
        }

        response = self.client.post(self.url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # 作成されたイベント詳細を確認
        created_detail = EventDetail.objects.get(speaker='Additional Info Speaker')
        self.assertEqual(
            created_detail.additional_info,
            'This is my additional information for the LT'
        )

    def test_update_event_detail_with_additional_info(self):
        """additional_infoを含むイベント詳細更新のテスト"""
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.api_key1.key}')

        url = reverse('event-detail-api-detail', kwargs={'pk': self.event_detail1.id})
        data = {
            'event': self.event1.id,
            'detail_type': 'LT',
            'start_time': '20:00:00',
            'duration': 30,
            'speaker': 'Speaker 1',
            'theme': 'Test Theme 1',
            'additional_info': 'Updated additional information'
        }

        response = self.client.put(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.event_detail1.refresh_from_db()
        self.assertEqual(
            self.event_detail1.additional_info,
            'Updated additional information'
        )