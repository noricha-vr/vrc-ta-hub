"""EventMyListビューのテスト（後方互換性およびダッシュボード機能）"""
from datetime import date, time, timedelta
from io import BytesIO
from PIL import Image

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from community.models import Community, CommunityMember
from event.models import Event

User = get_user_model()


def create_test_image():
    """テスト用の画像ファイルを生成する"""
    image = Image.new('RGB', (100, 100), color='red')
    buffer = BytesIO()
    image.save(buffer, format='JPEG')
    buffer.seek(0)
    return SimpleUploadedFile(
        name='test.jpg',
        content=buffer.read(),
        content_type='image/jpeg'
    )


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


class EventMyListDashboardTest(TestCase):
    """EventMyListダッシュボード機能のテスト"""

    def setUp(self):
        """テスト用データの準備"""
        self.client = Client()

        # ユーザー作成
        self.user = User.objects.create_user(
            user_name='Dashboard User',
            email='dashboard@example.com',
            password='dashboardpass123'
        )

        # 複数の集会を作成
        self.community1 = Community.objects.create(
            name='Community One',
            custom_user=self.user,
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Organizer 1',
            status='approved'
        )
        CommunityMember.objects.create(
            community=self.community1,
            user=self.user,
            role=CommunityMember.Role.OWNER
        )

        self.community2 = Community.objects.create(
            name='Community Two',
            start_time=time(21, 0),
            duration=90,
            weekdays=['Fri'],
            frequency='Every week',
            organizers='Organizer 2',
            status='approved'
        )
        CommunityMember.objects.create(
            community=self.community2,
            user=self.user,
            role=CommunityMember.Role.STAFF
        )

    def test_communities_in_context(self):
        """コンテキストに所属集会一覧が含まれる"""
        self.client.login(username='Dashboard User', password='dashboardpass123')
        response = self.client.get(reverse('event:my_list'))

        self.assertEqual(response.status_code, 200)
        communities = response.context['communities']
        community_ids = [c.id for c in communities]

        self.assertIn(self.community1.id, community_ids)
        self.assertIn(self.community2.id, community_ids)

    def test_active_community_in_context(self):
        """コンテキストにアクティブな集会が含まれる"""
        self.client.login(username='Dashboard User', password='dashboardpass123')
        response = self.client.get(reverse('event:my_list'))

        self.assertEqual(response.status_code, 200)
        active_community = response.context['active_community']
        self.assertIsNotNone(active_community)

    def test_switch_active_community(self):
        """集会切り替えが機能する"""
        self.client.login(username='Dashboard User', password='dashboardpass123')

        # community2をアクティブに設定
        response = self.client.post(
            reverse('community:switch'),
            {'community_id': self.community2.id},
            HTTP_REFERER=reverse('event:my_list')
        )

        # マイページにリダイレクトして確認
        response = self.client.get(reverse('event:my_list'))
        active_community = response.context['active_community']

        self.assertEqual(active_community.id, self.community2.id)

    def test_warnings_for_missing_poster(self):
        """ポスター未設定の警告が表示される"""
        self.client.login(username='Dashboard User', password='dashboardpass123')
        response = self.client.get(reverse('event:my_list'))

        self.assertEqual(response.status_code, 200)
        warnings = response.context['warnings']

        # ポスター未設定警告を検索
        poster_warning = None
        for w in warnings:
            if 'ポスター画像' in w['message']:
                poster_warning = w
                break

        self.assertIsNotNone(poster_warning)
        self.assertEqual(poster_warning['type'], 'warning')

    def test_warnings_for_no_future_events(self):
        """今後のイベントがない警告が表示される"""
        self.client.login(username='Dashboard User', password='dashboardpass123')
        response = self.client.get(reverse('event:my_list'))

        self.assertEqual(response.status_code, 200)
        warnings = response.context['warnings']

        # 今後のイベントなし警告を検索
        event_warning = None
        for w in warnings:
            if '今後のイベント' in w['message']:
                event_warning = w
                break

        self.assertIsNotNone(event_warning)
        self.assertEqual(event_warning['type'], 'info')

    def test_no_event_warning_when_future_events_exist(self):
        """未来のイベントがある場合は警告が表示されない"""
        # 未来のイベントを作成
        Event.objects.create(
            community=self.community1,
            date=date.today() + timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon'
        )

        self.client.login(username='Dashboard User', password='dashboardpass123')
        response = self.client.get(reverse('event:my_list'))

        warnings = response.context['warnings']

        # 今後のイベントなし警告がないことを確認
        event_warning = None
        for w in warnings:
            if '今後のイベント' in w['message']:
                event_warning = w
                break

        self.assertIsNone(event_warning)

    def test_no_poster_warning_when_poster_exists(self):
        """ポスター画像がある場合は警告が表示されない"""
        # ポスター画像を設定
        self.community1.poster_image = create_test_image()
        self.community1.save()

        self.client.login(username='Dashboard User', password='dashboardpass123')
        response = self.client.get(reverse('event:my_list'))

        warnings = response.context['warnings']

        # ポスター未設定警告がないことを確認
        poster_warning = None
        for w in warnings:
            if 'ポスター画像' in w['message']:
                poster_warning = w
                break

        self.assertIsNone(poster_warning)

    def test_dropdown_shows_when_multiple_communities(self):
        """複数の集会がある場合ドロップダウンが表示される"""
        self.client.login(username='Dashboard User', password='dashboardpass123')
        response = self.client.get(reverse('event:my_list'))

        self.assertEqual(response.status_code, 200)
        # ドロップダウンのHTMLが含まれる
        self.assertContains(response, 'dropdown-toggle')
        self.assertContains(response, 'Community One')
        self.assertContains(response, 'Community Two')

    def test_no_dropdown_with_single_community(self):
        """単一の集会の場合ドロップダウンは表示されない"""
        # 新しいユーザーと集会を作成（単一の集会のみ）
        single_user = User.objects.create_user(
            user_name='Single User',
            email='single@example.com',
            password='singlepass123'
        )
        single_community = Community.objects.create(
            name='Single Community',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Wed'],
            frequency='Every week',
            organizers='Single Organizer',
            status='approved'
        )
        CommunityMember.objects.create(
            community=single_community,
            user=single_user,
            role=CommunityMember.Role.OWNER
        )

        self.client.login(username='Single User', password='singlepass123')
        response = self.client.get(reverse('event:my_list'))

        self.assertEqual(response.status_code, 200)
        # 単一の集会の場合、コンテキストには1つだけ
        communities = response.context['communities']
        self.assertEqual(len(communities), 1)
        # 集会名は表示されるが、ドロップダウンとしてではない
        self.assertContains(response, 'Single Community')

    def test_quick_action_buttons_displayed(self):
        """クイックアクションボタンが表示される"""
        self.client.login(username='Dashboard User', password='dashboardpass123')
        response = self.client.get(reverse('event:my_list'))

        self.assertEqual(response.status_code, 200)
        # クイックアクションボタンのテキストが含まれる
        self.assertContains(response, 'イベント登録')
        self.assertContains(response, 'LT履歴')
        self.assertContains(response, '集会設定')
        self.assertContains(response, '公開ページ')
