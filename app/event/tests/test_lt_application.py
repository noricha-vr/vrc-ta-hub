"""LT申請機能のテスト"""
from datetime import date, time, timedelta
from io import BytesIO
from unittest.mock import patch

from PIL import Image
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core import mail

from community.models import Community, CommunityMember
from event.models import Event, EventDetail
from user_account.tests.utils import create_discord_linked_user

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


class LTApplicationFormTest(TestCase):
    """LT申請フォームのテスト"""

    def setUp(self):
        """テスト用データの準備"""
        self.client = Client()

        # Discord連携済みユーザー作成（ミドルウェアでリダイレクトされないため）
        self.user = create_discord_linked_user(
            user_name='TestUser',
            email='test@example.com',
            password='testpass123'
        )

        # 集会作成
        self.community = Community.objects.create(
            name='Test Community',
            custom_user=self.user,
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved'
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.user,
            role=CommunityMember.Role.OWNER
        )

        # 未来のイベント作成
        self.future_event = Event.objects.create(
            community=self.community,
            date=date.today() + timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon',
            accepts_lt_application=True
        )

        # LT受付しないイベント
        self.no_lt_event = Event.objects.create(
            community=self.community,
            date=date.today() + timedelta(days=14),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon',
            accepts_lt_application=False
        )

    def test_lt_application_form_displays(self):
        """LT申請フォームが正しく表示される"""
        self.client.login(username='TestUser', password='testpass123')
        url = reverse('event:lt_application_create', kwargs={'community_pk': self.community.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'LT発表を申請')
        self.assertContains(response, 'Test Community')

    def test_lt_application_form_requires_login(self):
        """未ログインユーザーはログインページにリダイレクトされる"""
        url = reverse('event:lt_application_create', kwargs={'community_pk': self.community.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        # django-allauthのログインURL
        self.assertTrue('login' in response.url.lower())

    def test_lt_application_only_shows_accepting_events(self):
        """LT受付中のイベントのみ選択肢に表示される"""
        self.client.login(username='TestUser', password='testpass123')
        url = reverse('event:lt_application_create', kwargs={'community_pk': self.community.pk})
        response = self.client.get(url)

        form = response.context['form']
        event_queryset = form.fields['event'].queryset

        # LT受付中のイベントのみ
        self.assertIn(self.future_event, event_queryset)
        self.assertNotIn(self.no_lt_event, event_queryset)

    @patch('event.notifications.send_mail')
    def test_lt_application_creates_event_detail(self, mock_send_mail):
        """LT申請でEventDetailが作成される"""
        mock_send_mail.return_value = 1
        self.client.login(username='TestUser', password='testpass123')

        url = reverse('event:lt_application_create', kwargs={'community_pk': self.community.pk})
        response = self.client.post(url, {
            'event': self.future_event.pk,
            'theme': 'Test Theme',
            'speaker': 'Test Speaker',
            'duration': 15,
        })

        # リダイレクト確認
        self.assertEqual(response.status_code, 302)

        # EventDetailが作成されたか確認
        event_detail = EventDetail.objects.filter(
            event=self.future_event,
            theme='Test Theme'
        ).first()

        self.assertIsNotNone(event_detail)
        self.assertEqual(event_detail.speaker, 'Test Speaker')
        self.assertEqual(event_detail.duration, 15)
        self.assertEqual(event_detail.status, 'pending')
        self.assertEqual(event_detail.applicant, self.user)


class LTApplicationReviewTest(TestCase):
    """LT申請の承認/却下テスト"""

    def setUp(self):
        """テスト用データの準備"""
        self.client = Client()

        # Discord連携済みの主催者
        self.owner = create_discord_linked_user(
            user_name='Owner',
            email='owner@example.com',
            password='ownerpass123'
        )

        # Discord連携済みの申請者
        self.applicant = create_discord_linked_user(
            user_name='Applicant',
            email='applicant@example.com',
            password='applicantpass123'
        )

        # 集会作成
        self.community = Community.objects.create(
            name='Test Community',
            custom_user=self.owner,
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved'
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner,
            role=CommunityMember.Role.OWNER
        )

        # イベント作成
        self.event = Event.objects.create(
            community=self.community,
            date=date.today() + timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon',
            accepts_lt_application=True
        )

        # 申請（pending状態のEventDetail）
        self.pending_application = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Test Theme',
            speaker='Test Speaker',
            duration=15,
            start_time=time(22, 0),
            status='pending',
            applicant=self.applicant
        )

    def test_review_page_requires_login(self):
        """レビューページは未ログインユーザーにはアクセスできない"""
        url = reverse('event:lt_application_review', kwargs={'pk': self.pending_application.pk})
        response = self.client.get(url)

        # ログインページにリダイレクトされる
        self.assertEqual(response.status_code, 302)
        self.assertTrue('login' in response.url.lower())

    def test_review_page_requires_permission(self):
        """レビューページは管理者権限が必要"""
        # 申請者（非管理者）でログイン
        self.client.login(username='Applicant', password='applicantpass123')
        url = reverse('event:lt_application_review', kwargs={'pk': self.pending_application.pk})
        response = self.client.get(url)

        # リダイレクトされる
        self.assertEqual(response.status_code, 302)

    def test_owner_can_access_review_page(self):
        """主催者はレビューページにアクセスできる"""
        self.client.login(username='Owner', password='ownerpass123')
        url = reverse('event:lt_application_review', kwargs={'pk': self.pending_application.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test Theme')

    @patch('event.notifications.send_mail')
    def test_approve_application(self, mock_send_mail):
        """申請を承認できる"""
        mock_send_mail.return_value = 1
        self.client.login(username='Owner', password='ownerpass123')

        url = reverse('event:lt_application_review', kwargs={'pk': self.pending_application.pk})
        response = self.client.post(url, {
            'action': 'approve',
            'rejection_reason': '',
        })

        # リダイレクト確認
        self.assertEqual(response.status_code, 302)

        # ステータス更新確認
        self.pending_application.refresh_from_db()
        self.assertEqual(self.pending_application.status, 'approved')

    @patch('event.notifications.send_mail')
    def test_reject_application(self, mock_send_mail):
        """申請を却下できる"""
        mock_send_mail.return_value = 1
        self.client.login(username='Owner', password='ownerpass123')

        url = reverse('event:lt_application_review', kwargs={'pk': self.pending_application.pk})
        response = self.client.post(url, {
            'action': 'reject',
            'rejection_reason': 'Test rejection reason',
        })

        # リダイレクト確認
        self.assertEqual(response.status_code, 302)

        # ステータス更新確認
        self.pending_application.refresh_from_db()
        self.assertEqual(self.pending_application.status, 'rejected')
        self.assertEqual(self.pending_application.rejection_reason, 'Test rejection reason')

    def test_reject_requires_reason(self):
        """却下時は理由が必要"""
        self.client.login(username='Owner', password='ownerpass123')

        url = reverse('event:lt_application_review', kwargs={'pk': self.pending_application.pk})
        response = self.client.post(url, {
            'action': 'reject',
            'rejection_reason': '',  # 空
        })

        # フォームエラーで再表示
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '却下する場合は理由を入力してください')


class LTApplicationListTest(TestCase):
    """LT申請一覧のテスト"""

    def setUp(self):
        """テスト用データの準備"""
        self.client = Client()

        # Discord連携済みの主催者
        self.owner = create_discord_linked_user(
            user_name='Owner',
            email='owner@example.com',
            password='ownerpass123'
        )

        # Discord連携済みの申請者
        self.applicant = create_discord_linked_user(
            user_name='Applicant',
            email='applicant@example.com',
            password='applicantpass123'
        )

        # 集会作成
        self.community = Community.objects.create(
            name='Test Community',
            custom_user=self.owner,
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved'
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner,
            role=CommunityMember.Role.OWNER
        )

        # イベント作成
        self.event = Event.objects.create(
            community=self.community,
            date=date.today() + timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon',
        )

        # 各ステータスの申請を作成
        self.pending_app = EventDetail.objects.create(
            event=self.event,
            theme='Pending Theme',
            speaker='Speaker 1',
            status='pending',
            applicant=self.applicant,
            duration=15,
            start_time=time(22, 0),
        )
        self.approved_app = EventDetail.objects.create(
            event=self.event,
            theme='Approved Theme',
            speaker='Speaker 2',
            status='approved',
            applicant=self.applicant,
            duration=15,
            start_time=time(22, 15),
        )
        self.rejected_app = EventDetail.objects.create(
            event=self.event,
            theme='Rejected Theme',
            speaker='Speaker 3',
            status='rejected',
            applicant=self.applicant,
            rejection_reason='Test reason',
            duration=15,
            start_time=time(22, 30),
        )

    def test_list_redirects_to_my_list(self):
        """申請一覧URLはマイリストにリダイレクトされる"""
        self.client.login(username='Owner', password='ownerpass123')
        url = reverse('community:lt_application_list', kwargs={'pk': self.community.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        self.assertIn('my_list', response.url)


class LTApplicationApproveRejectViewTest(TestCase):
    """LT申請の承認/却下ビュー（新API）のテスト"""

    def setUp(self):
        """テスト用データの準備"""
        self.client = Client()

        # Discord連携済みの主催者
        self.owner = create_discord_linked_user(
            user_name='Owner',
            email='owner@example.com',
            password='ownerpass123'
        )

        # Discord連携済みの申請者
        self.applicant = create_discord_linked_user(
            user_name='Applicant',
            email='applicant@example.com',
            password='applicantpass123'
        )

        # 集会作成
        self.community = Community.objects.create(
            name='Test Community',
            custom_user=self.owner,
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved'
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner,
            role=CommunityMember.Role.OWNER
        )

        # イベント作成
        self.event = Event.objects.create(
            community=self.community,
            date=date.today() + timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon',
            accepts_lt_application=True
        )

        # 申請（pending状態のEventDetail）
        self.pending_application = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Test Theme',
            speaker='Test Speaker',
            duration=15,
            start_time=time(22, 0),
            status='pending',
            applicant=self.applicant
        )

    @patch('event.notifications.send_mail')
    def test_approve_via_new_endpoint(self, mock_send_mail):
        """新しいエンドポイントで申請を承認できる"""
        mock_send_mail.return_value = 1
        self.client.login(username='Owner', password='ownerpass123')

        url = reverse('event:lt_application_approve', kwargs={'pk': self.pending_application.pk})
        response = self.client.post(url)

        # リダイレクト確認
        self.assertEqual(response.status_code, 302)

        # ステータス更新確認
        self.pending_application.refresh_from_db()
        self.assertEqual(self.pending_application.status, 'approved')

    @patch('event.notifications.send_mail')
    def test_reject_via_new_endpoint(self, mock_send_mail):
        """新しいエンドポイントで申請を却下できる"""
        mock_send_mail.return_value = 1
        self.client.login(username='Owner', password='ownerpass123')

        url = reverse('event:lt_application_reject', kwargs={'pk': self.pending_application.pk})
        response = self.client.post(url, {
            'rejection_reason': 'Test rejection reason',
        })

        # リダイレクト確認
        self.assertEqual(response.status_code, 302)

        # ステータス更新確認
        self.pending_application.refresh_from_db()
        self.assertEqual(self.pending_application.status, 'rejected')
        self.assertEqual(self.pending_application.rejection_reason, 'Test rejection reason')

    def test_reject_requires_reason(self):
        """却下時は理由が必要"""
        self.client.login(username='Owner', password='ownerpass123')

        url = reverse('event:lt_application_reject', kwargs={'pk': self.pending_application.pk})
        response = self.client.post(url, {
            'rejection_reason': '',  # 空
        })

        # リダイレクト（エラーメッセージ付き）
        self.assertEqual(response.status_code, 302)

        # ステータスは変更されていない
        self.pending_application.refresh_from_db()
        self.assertEqual(self.pending_application.status, 'pending')

    def test_approve_requires_permission(self):
        """承認には管理者権限が必要"""
        self.client.login(username='Applicant', password='applicantpass123')

        url = reverse('event:lt_application_approve', kwargs={'pk': self.pending_application.pk})
        response = self.client.post(url)

        # リダイレクト（権限エラー）
        self.assertEqual(response.status_code, 302)

        # ステータスは変更されていない
        self.pending_application.refresh_from_db()
        self.assertEqual(self.pending_application.status, 'pending')

    def test_reject_requires_permission(self):
        """却下には管理者権限が必要"""
        self.client.login(username='Applicant', password='applicantpass123')

        url = reverse('event:lt_application_reject', kwargs={'pk': self.pending_application.pk})
        response = self.client.post(url, {
            'rejection_reason': 'Test reason',
        })

        # リダイレクト（権限エラー）
        self.assertEqual(response.status_code, 302)

        # ステータスは変更されていない
        self.pending_application.refresh_from_db()
        self.assertEqual(self.pending_application.status, 'pending')

    def test_approve_already_processed(self):
        """既に処理済みの申請は承認できない"""
        self.pending_application.status = 'approved'
        self.pending_application.save()

        self.client.login(username='Owner', password='ownerpass123')

        url = reverse('event:lt_application_approve', kwargs={'pk': self.pending_application.pk})
        response = self.client.post(url)

        # リダイレクト
        self.assertEqual(response.status_code, 302)

    def test_reject_already_processed(self):
        """既に処理済みの申請は却下できない"""
        self.pending_application.status = 'approved'
        self.pending_application.save()

        self.client.login(username='Owner', password='ownerpass123')

        url = reverse('event:lt_application_reject', kwargs={'pk': self.pending_application.pk})
        response = self.client.post(url, {
            'rejection_reason': 'Test reason',
        })

        # リダイレクト
        self.assertEqual(response.status_code, 302)

        # ステータスは変更されていない（approvedのまま）
        self.pending_application.refresh_from_db()
        self.assertEqual(self.pending_application.status, 'approved')

    @patch('event.notifications.send_mail')
    def test_approve_ajax_response(self, mock_send_mail):
        """AJAX経由での承認時にJSONレスポンスが返る"""
        mock_send_mail.return_value = 1
        self.client.login(username='Owner', password='ownerpass123')

        url = reverse('event:lt_application_approve', kwargs={'pk': self.pending_application.pk})
        response = self.client.post(url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'success': True, 'status': 'approved'})

        # ステータス更新も確認
        self.pending_application.refresh_from_db()
        self.assertEqual(self.pending_application.status, 'approved')

    @patch('event.notifications.send_mail')
    def test_reject_ajax_response(self, mock_send_mail):
        """AJAX経由での却下時にJSONレスポンスが返る"""
        mock_send_mail.return_value = 1
        self.client.login(username='Owner', password='ownerpass123')

        url = reverse('event:lt_application_reject', kwargs={'pk': self.pending_application.pk})
        response = self.client.post(
            url,
            {'rejection_reason': 'Test rejection reason'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'success': True, 'status': 'rejected'})

        # ステータス更新も確認
        self.pending_application.refresh_from_db()
        self.assertEqual(self.pending_application.status, 'rejected')
        self.assertEqual(self.pending_application.rejection_reason, 'Test rejection reason')

    def test_approve_ajax_permission_error(self):
        """AJAX経由で権限エラー時にステータス403とJSONが返る"""
        self.client.login(username='Applicant', password='applicantpass123')

        url = reverse('event:lt_application_approve', kwargs={'pk': self.pending_application.pk})
        response = self.client.post(url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        self.assertEqual(response.status_code, 403)
        response_data = response.json()
        self.assertEqual(response_data['success'], False)
        self.assertIn('error', response_data)

        # ステータスは変更されていない
        self.pending_application.refresh_from_db()
        self.assertEqual(self.pending_application.status, 'pending')

    def test_reject_ajax_already_processed(self):
        """AJAX経由で既処理申請時にステータス400とJSONが返る"""
        self.pending_application.status = 'approved'
        self.pending_application.save()

        self.client.login(username='Owner', password='ownerpass123')

        url = reverse('event:lt_application_reject', kwargs={'pk': self.pending_application.pk})
        response = self.client.post(
            url,
            {'rejection_reason': 'Test reason'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )

        self.assertEqual(response.status_code, 400)
        response_data = response.json()
        self.assertEqual(response_data['success'], False)
        self.assertIn('error', response_data)

        # ステータスは変更されていない（approvedのまま）
        self.pending_application.refresh_from_db()
        self.assertEqual(self.pending_application.status, 'approved')


class CommunityDetailFilterTest(TestCase):
    """コミュニティ詳細ページでのEventDetailフィルタリングテスト"""

    def setUp(self):
        """テスト用データの準備"""
        self.client = Client()

        # ユーザー作成
        self.user = User.objects.create_user(
            user_name='TestUser',
            email='test@example.com',
            password='testpass123'
        )

        # 集会作成
        self.community = Community.objects.create(
            name='Test Community',
            custom_user=self.user,
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved'
        )

        # 未来のイベント作成
        self.event = Event.objects.create(
            community=self.community,
            date=date.today() + timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon'
        )

        # 承認済みのEventDetail
        self.approved_detail = EventDetail.objects.create(
            event=self.event,
            theme='Approved Theme',
            speaker='Approved Speaker',
            status='approved',
            duration=15,
            start_time=time(22, 0),
        )

        # 承認待ちのEventDetail
        self.pending_detail = EventDetail.objects.create(
            event=self.event,
            theme='Pending Theme',
            speaker='Pending Speaker',
            status='pending',
            applicant=self.user,
            duration=15,
            start_time=time(22, 15),
        )

    def test_community_detail_only_shows_approved_details(self):
        """コミュニティ詳細ページでは承認済みのEventDetailのみ表示"""
        url = reverse('community:detail', kwargs={'pk': self.community.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Approved Theme')
        self.assertNotContains(response, 'Pending Theme')


class EventModelTest(TestCase):
    """Eventモデルのテスト"""

    def setUp(self):
        """テスト用データの準備"""
        self.user = User.objects.create_user(
            user_name='TestUser',
            email='test@example.com',
            password='testpass123'
        )

        self.community = Community.objects.create(
            name='Test Community',
            custom_user=self.user,
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved'
        )

    def test_accepts_lt_application_default_true(self):
        """accepts_lt_applicationのデフォルト値はTrue"""
        event = Event.objects.create(
            community=self.community,
            date=date.today() + timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon'
        )

        self.assertTrue(event.accepts_lt_application)


class EventDetailModelTest(TestCase):
    """EventDetailモデルのテスト"""

    def setUp(self):
        """テスト用データの準備"""
        self.user = User.objects.create_user(
            user_name='TestUser',
            email='test@example.com',
            password='testpass123'
        )

        self.community = Community.objects.create(
            name='Test Community',
            custom_user=self.user,
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved'
        )

        self.event = Event.objects.create(
            community=self.community,
            date=date.today() + timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon'
        )

    def test_status_default_approved(self):
        """statusのデフォルト値はapproved"""
        detail = EventDetail.objects.create(
            event=self.event,
            theme='Test Theme',
            speaker='Test Speaker',
            duration=15,
            start_time=time(22, 0),
        )

        self.assertEqual(detail.status, 'approved')

    def test_applicant_can_be_null(self):
        """applicantはnull可"""
        detail = EventDetail.objects.create(
            event=self.event,
            theme='Test Theme',
            speaker='Test Speaker',
            duration=15,
            start_time=time(22, 0),
        )

        self.assertIsNone(detail.applicant)
