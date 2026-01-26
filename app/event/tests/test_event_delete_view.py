"""EventDeleteViewの権限テスト"""
from datetime import date, time

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from community.models import Community, CommunityMember
from event.models import Event

User = get_user_model()


class EventDeleteViewPermissionTest(TestCase):
    """EventDeleteViewの権限チェックテスト"""

    def setUp(self):
        """テスト用データの準備"""
        self.client = Client()

        # ユーザー作成
        self.owner_user = User.objects.create_user(
            user_name='Owner User',
            email='owner@example.com',
            password='ownerpass123'
        )
        self.staff_user = User.objects.create_user(
            user_name='Staff User',
            email='staff@example.com',
            password='staffpass123'
        )
        self.other_user = User.objects.create_user(
            user_name='Other User',
            email='other@example.com',
            password='otherpass123'
        )

        # コミュニティ作成
        self.community = Community.objects.create(
            name='Test Community',
            custom_user=self.owner_user,
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved'
        )

        # CommunityMemberを作成
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner_user,
            role=CommunityMember.Role.OWNER
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.staff_user,
            role=CommunityMember.Role.STAFF
        )

        # イベント作成
        self.event = Event.objects.create(
            community=self.community,
            date=date(2025, 2, 1),
            start_time=time(22, 0),
            duration=60,
            weekday='Sat'
        )

    def test_owner_can_delete_event(self):
        """主催者はイベントを削除できる"""
        self.client.login(username='Owner User', password='ownerpass123')

        url = reverse('event:delete', kwargs={'pk': self.event.pk})
        response = self.client.post(url)

        # リダイレクトされることを確認
        self.assertEqual(response.status_code, 302)

        # イベントが削除されたことを確認
        self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())

    def test_staff_cannot_delete_event(self):
        """スタッフはイベントを削除できない"""
        self.client.login(username='Staff User', password='staffpass123')

        url = reverse('event:delete', kwargs={'pk': self.event.pk})
        response = self.client.post(url)

        # リダイレクトされることを確認
        self.assertEqual(response.status_code, 302)

        # イベントが削除されていないことを確認
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())

    def test_other_user_cannot_delete_event(self):
        """コミュニティ外のユーザーはイベントを削除できない"""
        self.client.login(username='Other User', password='otherpass123')

        url = reverse('event:delete', kwargs={'pk': self.event.pk})
        response = self.client.post(url)

        # リダイレクトされることを確認
        self.assertEqual(response.status_code, 302)

        # イベントが削除されていないことを確認
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())

    def test_anonymous_user_redirected_to_login(self):
        """未ログインユーザーはログインページにリダイレクトされる"""
        url = reverse('event:delete', kwargs={'pk': self.event.pk})
        response = self.client.post(url)

        # ログインページにリダイレクトされることを確認
        self.assertEqual(response.status_code, 302)
        self.assertIn('/account/login/', response.url)

        # イベントが削除されていないことを確認
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())


class EventDeleteViewMultipleOwnersTest(TestCase):
    """複数主催者がいる場合のEventDeleteViewテスト"""

    def setUp(self):
        """テスト用データの準備"""
        self.client = Client()

        # ユーザー作成
        self.owner1 = User.objects.create_user(
            user_name='Owner1 User',
            email='owner1@example.com',
            password='owner1pass123'
        )
        self.owner2 = User.objects.create_user(
            user_name='Owner2 User',
            email='owner2@example.com',
            password='owner2pass123'
        )

        # コミュニティ作成
        self.community = Community.objects.create(
            name='Multi Owner Community',
            custom_user=self.owner1,
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved'
        )

        # 複数の主催者を設定
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner1,
            role=CommunityMember.Role.OWNER
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner2,
            role=CommunityMember.Role.OWNER
        )

        # イベント作成
        self.event = Event.objects.create(
            community=self.community,
            date=date(2025, 2, 1),
            start_time=time(22, 0),
            duration=60,
            weekday='Sat'
        )

    def test_second_owner_can_delete_event(self):
        """2人目の主催者もイベントを削除できる"""
        self.client.login(username='Owner2 User', password='owner2pass123')

        url = reverse('event:delete', kwargs={'pk': self.event.pk})
        response = self.client.post(url)

        # リダイレクトされることを確認
        self.assertEqual(response.status_code, 302)

        # イベントが削除されたことを確認
        self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())
