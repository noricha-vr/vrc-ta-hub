"""CalendarEntryUpdateViewのテスト"""
import datetime

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from community.models import Community, CommunityMember

CustomUser = get_user_model()


class CalendarEntryUpdateViewTest(TestCase):
    """CalendarEntryUpdateViewのテスト"""

    def setUp(self):
        self.client = Client()
        # ユーザーを作成
        self.owner = CustomUser.objects.create_user(
            user_name="owner_user",
            email="owner@example.com",
            password="testpassword"
        )
        self.staff = CustomUser.objects.create_user(
            user_name="staff_user",
            email="staff@example.com",
            password="testpassword"
        )
        self.other_user = CustomUser.objects.create_user(
            user_name="other_user",
            email="other@example.com",
            password="testpassword"
        )

        # 集会を作成
        self.community = Community.objects.create(
            custom_user=self.owner,
            name="Test Community",
            start_time=datetime.time(21, 0),
            duration=60,
            weekdays=["Mon"],
            frequency="Weekly",
            organizers="Test Organizer",
            description="Test Description",
            platform="All",
            status="approved"
        )

        # メンバーシップを作成
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner,
            role=CommunityMember.Role.OWNER
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.staff,
            role=CommunityMember.Role.STAFF
        )

    def test_anonymous_user_redirected_to_login(self):
        """未ログインユーザーはログインページにリダイレクトされる"""
        url = reverse('community:calendar_update')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/account/login/', response.url)

    def test_owner_can_access_calendar_entry(self):
        """主催者はカレンダーエントリーにアクセスできる"""
        self.client.login(username='owner_user', password='testpassword')
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

        url = reverse('community:calendar_update')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_staff_can_access_calendar_entry(self):
        """スタッフもカレンダーエントリーにアクセスできる"""
        self.client.login(username='staff_user', password='testpassword')
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

        url = reverse('community:calendar_update')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_non_member_cannot_access_calendar_entry(self):
        """メンバーでないユーザーはアクセスできない"""
        self.client.login(username='other_user', password='testpassword')
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

        url = reverse('community:calendar_update')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_no_active_community_returns_404(self):
        """active_community_idがない場合は404を返す"""
        self.client.login(username='owner_user', password='testpassword')
        # セッションを空にする（active_community_idをセットしない）
        session = self.client.session
        if 'active_community_id' in session:
            del session['active_community_id']
        session.save()

        url = reverse('community:calendar_update')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_calendar_entry_created_if_not_exists(self):
        """カレンダーエントリーが存在しない場合は作成される"""
        from event_calendar.models import CalendarEntry

        # 最初はエントリーが存在しないことを確認
        self.assertEqual(CalendarEntry.objects.filter(community=self.community).count(), 0)

        self.client.login(username='owner_user', password='testpassword')
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

        url = reverse('community:calendar_update')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # エントリーが作成されたことを確認
        self.assertEqual(CalendarEntry.objects.filter(community=self.community).count(), 1)


class CalendarEntryUpdateViewBackwardCompatibilityTest(TestCase):
    """CalendarEntryUpdateViewの後方互換性テスト"""

    def setUp(self):
        self.client = Client()
        # CommunityMember未作成の旧データを模倣
        self.owner = CustomUser.objects.create_user(
            user_name="legacy_owner",
            email="legacy@example.com",
            password="testpassword"
        )
        self.community = Community.objects.create(
            custom_user=self.owner,
            name="Legacy Community",
            start_time=datetime.time(21, 0),
            duration=60,
            weekdays=["Mon"],
            frequency="Weekly",
            organizers="Test Organizer",
            description="Test Description",
            platform="All",
            status="approved"
        )
        # 注意: CommunityMemberは作成しない

    def test_legacy_owner_can_access_via_is_manager_fallback(self):
        """
        CommunityMember未作成でも、custom_userはis_manager()のフォールバックで
        アクセス可能（後方互換性を確認）
        """
        self.client.login(username='legacy_owner', password='testpassword')
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

        url = reverse('community:calendar_update')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
