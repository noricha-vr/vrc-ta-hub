"""EventMyListビューの後方互換性テスト"""
from datetime import date, time, timedelta

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from community.models import Community, CommunityMember
from event.models import Event

User = get_user_model()


class EventMyListBackwardCompatibilityTest(TestCase):
    """CommunityMember未作成の集会に対するEventMyListの後方互換性テスト"""

    def setUp(self):
        """テスト用データの準備"""
        self.client = Client()

        # レガシーオーナー（CommunityMemberなしで集会を持つ）
        self.legacy_owner = User.objects.create_user(
            user_name='Legacy Owner',
            email='legacy@example.com',
            password='legacypass123'
        )

        # 通常オーナー（CommunityMemberありで集会を持つ）
        self.normal_owner = User.objects.create_user(
            user_name='Normal Owner',
            email='normal@example.com',
            password='normalpass123'
        )

        # レガシー集会（CommunityMemberなし）
        self.legacy_community = Community.objects.create(
            name='Legacy Community',
            custom_user=self.legacy_owner,
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Legacy Organizer',
            status='approved'
        )
        # 意図的にCommunityMemberを作成しない

        # 通常の集会（CommunityMemberあり）
        self.normal_community = Community.objects.create(
            name='Normal Community',
            custom_user=self.normal_owner,
            start_time=time(22, 0),
            duration=60,
            weekdays=['Tue'],
            frequency='Every week',
            organizers='Normal Organizer',
            status='approved'
        )
        CommunityMember.objects.create(
            community=self.normal_community,
            user=self.normal_owner,
            role=CommunityMember.Role.OWNER
        )

        # 未来の日付でイベントを作成
        future_date = date.today() + timedelta(days=7)

        self.legacy_event = Event.objects.create(
            community=self.legacy_community,
            date=future_date,
            start_time=time(22, 0),
            duration=60,
            weekday='Mon'
        )

        self.normal_event = Event.objects.create(
            community=self.normal_community,
            date=future_date,
            start_time=time(22, 0),
            duration=60,
            weekday='Tue'
        )

    def test_legacy_owner_can_see_events_in_my_list(self):
        """CommunityMember未作成でもcustom_userはMyListでイベントを確認できる"""
        # CommunityMemberが存在しないことを確認
        self.assertFalse(
            CommunityMember.objects.filter(community=self.legacy_community).exists()
        )

        self.client.login(username='Legacy Owner', password='legacypass123')
        url = reverse('event:my_list')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        # レガシー集会のイベントが表示される
        self.assertContains(response, 'Legacy Community')

    def test_normal_owner_can_see_events_in_my_list(self):
        """CommunityMemberありのオーナーはMyListでイベントを確認できる"""
        self.client.login(username='Normal Owner', password='normalpass123')
        url = reverse('event:my_list')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        # 通常の集会のイベントが表示される
        self.assertContains(response, 'Normal Community')

    def test_legacy_owner_events_in_queryset(self):
        """get_querysetがcustom_userベースの集会のイベントを含む"""
        self.client.login(username='Legacy Owner', password='legacypass123')
        url = reverse('event:my_list')
        response = self.client.get(url)

        # コンテキストからeventsを取得
        events = response.context['events']
        event_ids = [e.id for e in events]

        # レガシー集会のイベントが含まれている
        self.assertIn(self.legacy_event.id, event_ids)
