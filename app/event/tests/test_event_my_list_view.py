"""EventMyListビューのテスト（後方互換性およびダッシュボード機能）"""
from datetime import date, time, timedelta
from io import BytesIO
from PIL import Image

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from community.models import Community, CommunityMember
from event.models import Event, EventDetail

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

    def test_pending_lt_shows_approve_reject_buttons(self):
        """承認待ちLT申請に承認・却下ボタンが表示される"""
        event = Event.objects.create(
            community=self.community1,
            date=date.today() + timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon'
        )
        detail = EventDetail.objects.create(
            event=event,
            theme='テスト発表',
            speaker='テスト発表者',
            start_time=time(22, 30),
            applicant=self.user,
            status='pending',
        )

        self.client.login(username='Dashboard User', password='dashboardpass123')
        response = self.client.get(reverse('event:my_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '承認待ち')
        self.assertContains(response, f'approveModal{detail.pk}')
        self.assertContains(response, f'rejectModal{detail.pk}')

    def test_pending_lt_modals_rendered_outside_card(self):
        """承認・却下モーダルがcard要素の外に出力される（CSSトランジション干渉回避）"""
        event = Event.objects.create(
            community=self.community1,
            date=date.today() + timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon'
        )
        detail = EventDetail.objects.create(
            event=event,
            theme='モーダル配置テスト',
            speaker='テスト発表者',
            start_time=time(22, 30),
            applicant=self.user,
            status='pending',
        )

        self.client.login(username='Dashboard User', password='dashboardpass123')
        response = self.client.get(reverse('event:my_list'))
        content = response.content.decode()

        # モーダルDOMがcard-body内ではなく、paginationの後に配置されていることを確認
        modal_approve_pos = content.find(f'id="approveModal{detail.pk}"')
        modal_reject_pos = content.find(f'id="rejectModal{detail.pk}"')

        # モーダルが存在することを確認
        self.assertGreater(modal_approve_pos, -1)
        self.assertGreater(modal_reject_pos, -1)

        # モーダルがpaginationの後に出力されていることを確認
        pagination_pos = content.find('pagination')
        self.assertGreater(modal_approve_pos, pagination_pos)
        self.assertGreater(modal_reject_pos, pagination_pos)

    def test_approved_lt_no_modals(self):
        """承認済みLT申請にはモーダルが出力されない"""
        event = Event.objects.create(
            community=self.community1,
            date=date.today() + timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon'
        )
        EventDetail.objects.create(
            event=event,
            theme='承認済み発表',
            speaker='テスト発表者',
            start_time=time(22, 30),
            applicant=self.user,
            status='approved',
        )

        self.client.login(username='Dashboard User', password='dashboardpass123')
        response = self.client.get(reverse('event:my_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '承認済み')
        self.assertNotContains(response, 'approveModal')
        self.assertNotContains(response, 'rejectModal')
