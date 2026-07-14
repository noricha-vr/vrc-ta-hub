"""LT申請機能のテスト"""
import re
from datetime import date, time, timedelta
from io import BytesIO
from unittest.mock import patch

from PIL import Image
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from community.models import Community, CommunityMember
from event.models import Event, EventDetail
from event.tests.tweet_generation import TweetGenerationPatchMixin
from event.views.lt_application import _calc_next_lt_start_time
from tests.factories import make_community, make_event, make_event_detail
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


class LTApplicationFormTest(TweetGenerationPatchMixin, TestCase):
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

        # 集会作成（オーナーとして TestUser を紐づけ）
        self.community = make_community(owner=self.user)

        # 未来のイベント作成
        self.future_event = make_event(self.community)

        # LT受付しないイベント
        self.no_lt_event = make_event(
            self.community,
            event_date=date.today() + timedelta(days=14),
            accepts_lt_application=False,
        )

    def test_lt_application_form_displays(self):
        """LT申請フォームが正しく表示される"""
        self.client.login(username='TestUser', password='testpass123')
        url = reverse('event:lt_application_create', kwargs={'community_pk': self.community.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '発表を申請')
        self.assertContains(response, 'Test Community')
        self.assertContains(
            response,
            '発表の持ち時間: 30分（発表と質疑応答を含む）',
        )
        self.assertContains(
            response,
            '開始時刻や持ち時間の変更を希望する場合は、追加情報（備考）欄にご記入ください。主催者が承認時に調整します。',
        )
        self.assertNotContains(response, 'id="id_duration"')

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
            'speaker': 'TestSpeaker',
        })

        # リダイレクト確認
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(
            response,
            reverse(
                'event:lt_application_complete',
                kwargs={'community_pk': self.community.pk},
            ),
        )

        # EventDetailが作成されたか確認
        event_detail = EventDetail.objects.filter(
            event=self.future_event,
            theme='Test Theme'
        ).first()

        self.assertIsNotNone(event_detail)
        self.assertEqual(event_detail.speaker, 'TestSpeaker')
        self.assertEqual(event_detail.duration, self.community.default_lt_duration)
        self.assertEqual(event_detail.status, 'pending')
        self.assertEqual(event_detail.applicant, self.user)

    @patch('event.notifications.send_mail')
    def test_lt_application_uses_default_offset_30(self, mock_send_mail):
        """デフォルトのオフセット 30 分で LT 開始時刻が計算される"""
        mock_send_mail.return_value = 1
        # デフォルト値は 30。明示的に確認
        self.community.lt_start_offset_minutes = 30
        self.community.save(update_fields=['lt_start_offset_minutes'])

        self.client.login(username='TestUser', password='testpass123')
        url = reverse('event:lt_application_create', kwargs={'community_pk': self.community.pk})
        self.client.post(url, {
            'event': self.future_event.pk,
            'theme': 'Offset30',
            'speaker': 'Speaker',
        })

        event_detail = EventDetail.objects.get(event=self.future_event, theme='Offset30')
        # event.start_time = 22:00、オフセット30分 → 22:30
        self.assertEqual(event_detail.start_time, time(22, 30))

    @patch('event.notifications.send_mail')
    def test_lt_application_ignores_posted_duration(self, mock_send_mail):
        """POSTされた持ち時間ではなく集会のデフォルトを保存する。"""
        mock_send_mail.return_value = 1
        self.client.login(username='TestUser', password='testpass123')
        url = reverse('event:lt_application_create', kwargs={'community_pk': self.community.pk})

        response = self.client.post(url, {
            'event': self.future_event.pk,
            'theme': 'Duration Override Attempt',
            'speaker': 'Speaker',
            'duration': 5,
        })

        self.assertEqual(response.status_code, 302)
        event_detail = EventDetail.objects.get(
            event=self.future_event,
            theme='Duration Override Attempt',
        )
        self.assertEqual(event_detail.duration, self.community.default_lt_duration)

    def test_lt_application_complete_page_displays_slide_video_flow(self):
        """申請完了ページに承認後の発表準備フローが表示される"""
        self.client.login(username='TestUser', password='testpass123')
        url = reverse(
            'event:lt_application_complete',
            kwargs={'community_pk': self.community.pk},
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '発表を申請しました。主催者の承認をお待ちください。')
        self.assertContains(response, 'PDFで書き出す')
        self.assertContains(response, 'WebScreenで動画に変換')
        self.assertContains(response, 'https://web-screen.net/ja/pdf/')
        self.assertContains(response, 'URLをスライドオブジェクトへ')
        self.assertContains(response, '/guide/speaker/slide-video/')

    def test_lt_application_complete_page_requires_login(self):
        """未ログインユーザーは申請完了ページからログインページへリダイレクトされる"""
        url = reverse(
            'event:lt_application_complete',
            kwargs={'community_pk': self.community.pk},
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        self.assertTrue('login' in response.url.lower())

    @patch('event.notifications.send_mail')
    def test_lt_application_uses_custom_offset(self, mock_send_mail):
        """カスタムオフセットが LT 開始時刻に反映される"""
        mock_send_mail.return_value = 1
        self.community.lt_start_offset_minutes = 45
        self.community.save(update_fields=['lt_start_offset_minutes'])

        self.client.login(username='TestUser', password='testpass123')
        url = reverse('event:lt_application_create', kwargs={'community_pk': self.community.pk})
        self.client.post(url, {
            'event': self.future_event.pk,
            'theme': 'Offset45',
            'speaker': 'Speaker',
        })

        event_detail = EventDetail.objects.get(event=self.future_event, theme='Offset45')
        # event.start_time = 22:00、オフセット45分 → 22:45
        self.assertEqual(event_detail.start_time, time(22, 45))

    @patch('event.notifications.send_mail')
    def test_lt_application_offset_zero(self, mock_send_mail):
        """オフセット 0 のときは event.start_time と一致（旧挙動と同等）"""
        mock_send_mail.return_value = 1
        self.community.lt_start_offset_minutes = 0
        self.community.save(update_fields=['lt_start_offset_minutes'])

        self.client.login(username='TestUser', password='testpass123')
        url = reverse('event:lt_application_create', kwargs={'community_pk': self.community.pk})
        self.client.post(url, {
            'event': self.future_event.pk,
            'theme': 'Offset0',
            'speaker': 'Speaker',
        })

        event_detail = EventDetail.objects.get(event=self.future_event, theme='Offset0')
        self.assertEqual(event_detail.start_time, time(22, 0))

    @patch('event.notifications.send_mail')
    def test_lt_application_assigns_start_time_after_pending_lt(self, mock_send_mail):
        """承認待ちLTの終了時刻を次のLTの開始時刻にする。"""
        mock_send_mail.return_value = 1
        make_event_detail(
            self.future_event,
            theme='First LT',
            start_time=time(22, 30),
            duration=15,
            status='pending',
        )
        self.client.login(username='TestUser', password='testpass123')
        url = reverse('event:lt_application_create', kwargs={'community_pk': self.community.pk})

        self.client.post(url, {
            'event': self.future_event.pk,
            'theme': 'Second LT',
            'speaker': 'Speaker',
        })
        second_lt = EventDetail.objects.get(event=self.future_event, theme='Second LT')
        self.assertEqual(second_lt.start_time, time(22, 45))

        self.client.post(url, {
            'event': self.future_event.pk,
            'theme': 'Third LT',
            'speaker': 'Speaker',
        })
        third_lt = EventDetail.objects.get(event=self.future_event, theme='Third LT')
        self.assertEqual(third_lt.start_time, time(23, 15))

    @patch('event.notifications.send_mail')
    def test_lt_application_ignores_rejected_lt_for_start_time(self, mock_send_mail):
        """却下済みLTは次の開始時刻の計算対象から除外する。"""
        mock_send_mail.return_value = 1
        make_event_detail(
            self.future_event,
            theme='Rejected LT',
            start_time=time(23, 0),
            duration=15,
            status='rejected',
        )
        self.client.login(username='TestUser', password='testpass123')
        url = reverse('event:lt_application_create', kwargs={'community_pk': self.community.pk})

        self.client.post(url, {
            'event': self.future_event.pk,
            'theme': 'New LT',
            'speaker': 'Speaker',
        })

        new_lt = EventDetail.objects.get(event=self.future_event, theme='New LT')
        self.assertEqual(new_lt.start_time, time(22, 30))

    @patch('event.notifications.send_mail')
    def test_lt_application_uses_latest_existing_lt_end_time(self, mock_send_mail):
        """LT間に空きがあっても最も遅い終了時刻の後に割り当てる。"""
        mock_send_mail.return_value = 1
        make_event_detail(
            self.future_event,
            theme='Early LT',
            start_time=time(22, 30),
            duration=15,
            status='approved',
        )
        make_event_detail(
            self.future_event,
            theme='Late LT',
            start_time=time(23, 20),
            duration=10,
            status='approved',
        )
        self.client.login(username='TestUser', password='testpass123')
        url = reverse('event:lt_application_create', kwargs={'community_pk': self.community.pk})

        self.client.post(url, {
            'event': self.future_event.pk,
            'theme': 'New LT',
            'speaker': 'Speaker',
        })

        new_lt = EventDetail.objects.get(event=self.future_event, theme='New LT')
        self.assertEqual(new_lt.start_time, time(23, 30))

    @patch('event.notifications.send_mail')
    def test_lt_application_handles_start_time_across_midnight(self, mock_send_mail):
        """日付をまたぐLTでもイベント開始時刻からの経過時間で順序付ける。"""
        mock_send_mail.return_value = 1
        overnight_event = make_event(
            self.community,
            event_date=date.today() + timedelta(days=8),
            start_time=time(23, 50),
        )
        make_event_detail(
            overnight_event,
            theme='Overnight LT',
            start_time=time(23, 50),
            duration=30,
            status='pending',
        )
        self.client.login(username='TestUser', password='testpass123')
        url = reverse('event:lt_application_create', kwargs={'community_pk': self.community.pk})

        self.client.post(url, {
            'event': overnight_event.pk,
            'theme': 'After Midnight LT',
            'speaker': 'Speaker',
        })

        new_lt = EventDetail.objects.get(event=overnight_event, theme='After Midnight LT')
        self.assertEqual(new_lt.start_time, time(0, 20))

    def test_lt_application_context_includes_next_start_times(self):
        """画面用コンテキストに開催日ごとの開始予定時刻を含める。"""
        make_event_detail(
            self.future_event,
            theme='Existing LT',
            start_time=time(22, 30),
            duration=15,
            status='approved',
        )
        self.client.login(username='TestUser', password='testpass123')
        url = reverse('event:lt_application_create', kwargs={'community_pk': self.community.pk})

        response = self.client.get(url)

        self.assertEqual(response.context['next_start_times'][self.future_event.pk], '22:45')
        self.assertContains(response, 'id="next-start-times"')

    def test_calc_next_lt_start_time_uses_offset_without_existing_lt(self):
        """既存LTがない場合は従来どおりオフセット後を返す。"""
        self.assertEqual(
            _calc_next_lt_start_time(time(22, 0), [], 30),
            time(22, 30),
        )

    def test_calc_next_lt_start_time_ignores_lt_ending_before_event_offset(self):
        """開始前に終了する手動配置LTでもオフセットを下回らない。"""
        self.assertEqual(
            _calc_next_lt_start_time(
                time(21, 0),
                [(time(20, 0), 30)],
                30,
            ),
            time(21, 30),
        )

    def test_calc_next_lt_start_time_floors_early_existing_lt_at_offset(self):
        """既存LTの終了がオフセットより早い場合はオフセットから開始する。"""
        self.assertEqual(
            _calc_next_lt_start_time(
                time(21, 0),
                [(time(21, 5), 10)],
                30,
            ),
            time(21, 30),
        )


class LTApplicationReviewTest(TweetGenerationPatchMixin, TestCase):
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

        # 集会・イベント作成
        self.community = make_community(owner=self.owner)
        self.event = make_event(self.community)

        # 申請（pending状態のEventDetail）
        self.pending_application = make_event_detail(
            self.event,
            applicant=self.applicant,
            theme='Test Theme',
            speaker='Test Speaker',
            duration=15,
            status='pending',
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

    def test_owner_can_view_approved_application(self):
        """承認済み申請も主催者は閲覧でき、追加情報が表示される"""
        self.pending_application.status = 'approved'
        self.pending_application.additional_info = 'Discord ID: somnicat#1234\n配信時注意: BGM注意'
        self.pending_application.save()

        self.client.login(username='Owner', password='ownerpass123')
        url = reverse('event:lt_application_review', kwargs={'pk': self.pending_application.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test Theme')
        self.assertContains(response, 'Discord ID: somnicat#1234')
        self.assertContains(response, '承認済み')
        # 承認/却下フォームは表示されない
        self.assertNotContains(response, 'name="action"')

    def test_owner_can_view_rejected_application_with_reason(self):
        """却下済み申請も閲覧でき、却下理由が表示される"""
        self.pending_application.status = 'rejected'
        self.pending_application.rejection_reason = 'テーマが集会の趣旨に合いません'
        self.pending_application.save()

        self.client.login(username='Owner', password='ownerpass123')
        url = reverse('event:lt_application_review', kwargs={'pk': self.pending_application.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'テーマが集会の趣旨に合いません')
        self.assertContains(response, '却下')
        self.assertNotContains(response, 'name="action"')

    def test_post_to_processed_application_is_blocked(self):
        """処理済み申請への POST はリダイレクトされ、状態は変わらない"""
        self.pending_application.status = 'approved'
        self.pending_application.save()

        self.client.login(username='Owner', password='ownerpass123')
        url = reverse('event:lt_application_review', kwargs={'pk': self.pending_application.pk})
        response = self.client.post(url, {
            'action': 'reject',
            'rejection_reason': '気が変わった',
        })

        # review ページにリダイレクトされる（my_list ではなく自身に戻して閲覧継続）
        self.assertEqual(response.status_code, 302)
        self.pending_application.refresh_from_db()
        # 既に approved のまま、上書きされない
        self.assertEqual(self.pending_application.status, 'approved')


class LTApplicationListTest(TweetGenerationPatchMixin, TestCase):
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

        # 集会・イベント作成（accepts_lt_application は元コードと同様デフォルトのまま）
        self.community = make_community(owner=self.owner)
        self.event = make_event(self.community)

        # 各ステータスの申請を作成（時刻は元コード通り）
        self.pending_app = make_event_detail(
            self.event,
            applicant=self.applicant,
            theme='Pending Theme',
            speaker='Speaker 1',
            duration=15,
            status='pending',
            start_time=time(22, 0),
        )
        self.approved_app = make_event_detail(
            self.event,
            applicant=self.applicant,
            theme='Approved Theme',
            speaker='Speaker 2',
            duration=15,
            status='approved',
            start_time=time(22, 15),
        )
        self.rejected_app = make_event_detail(
            self.event,
            applicant=self.applicant,
            theme='Rejected Theme',
            speaker='Speaker 3',
            duration=15,
            status='rejected',
            rejection_reason='Test reason',
            start_time=time(22, 30),
        )

    def test_list_redirects_to_my_list(self):
        """申請一覧URLはマイリストにリダイレクトされる"""
        self.client.login(username='Owner', password='ownerpass123')
        url = reverse('community:lt_application_list', kwargs={'pk': self.community.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        self.assertIn('my_list', response.url)


class LTApplicationApproveRejectViewTest(TweetGenerationPatchMixin, TestCase):
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


class CommunityDetailFilterTest(TweetGenerationPatchMixin, TestCase):
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


class EventModelTest(TweetGenerationPatchMixin, TestCase):
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


class EventDetailModelTest(TweetGenerationPatchMixin, TestCase):
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

    def test_additional_info_default_empty(self):
        """additional_infoのデフォルト値は空文字"""
        detail = EventDetail.objects.create(
            event=self.event,
            theme='Test Theme',
            speaker='Test Speaker',
            duration=15,
            start_time=time(22, 0),
        )

        self.assertEqual(detail.additional_info, '')


class LTApplicationAdditionalInfoTest(TweetGenerationPatchMixin, TestCase):
    """LT申請の追加情報フィールドのテスト"""

    def setUp(self):
        """テスト用データの準備"""
        self.client = Client()

        # Discord連携済みユーザー作成
        self.user = create_discord_linked_user(
            user_name='TestUser',
            email='test@example.com',
            password='testpass123'
        )

        # テンプレート付き集会作成
        self.community_with_template = Community.objects.create(
            name='Community With Template',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved',
            lt_application_template='【発表概要】\n\n【対象者】\n',
        )
        CommunityMember.objects.create(
            community=self.community_with_template,
            user=self.user,
            role=CommunityMember.Role.OWNER
        )

        # テンプレートなし集会作成
        self.community_without_template = Community.objects.create(
            name='Community Without Template',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Tue'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved',
            lt_application_template='',
        )
        CommunityMember.objects.create(
            community=self.community_without_template,
            user=self.user,
            role=CommunityMember.Role.OWNER
        )

        # 未来のイベント作成
        self.event_with_template = Event.objects.create(
            community=self.community_with_template,
            date=date.today() + timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon',
            accepts_lt_application=True
        )

        self.event_without_template = Event.objects.create(
            community=self.community_without_template,
            date=date.today() + timedelta(days=8),
            start_time=time(22, 0),
            duration=60,
            weekday='Tue',
            accepts_lt_application=True
        )

    def test_additional_info_field_shown_with_template(self):
        """テンプレートがある集会では初期値入りで追加情報フィールドが表示される"""
        self.client.login(username='TestUser', password='testpass123')
        url = reverse('event:lt_application_create', kwargs={'community_pk': self.community_with_template.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '追加情報')
        template = self.community_with_template.lt_application_template
        form = response.context['form']
        field = form.fields['additional_info']
        self.assertEqual(field.initial, template)
        self.assertNotIn('placeholder', field.widget.attrs)
        self.assertRegex(
            response.content.decode(),
            rf'<textarea[^>]*name="additional_info"[^>]*>\s*{re.escape(template)}</textarea>',
        )

    def test_additional_info_field_shown_without_template(self):
        """テンプレートがない集会でも追加情報フィールドが表示される"""
        self.client.login(username='TestUser', password='testpass123')
        url = reverse('event:lt_application_create', kwargs={'community_pk': self.community_without_template.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertIn('additional_info', form.fields)
        self.assertNotIn('placeholder', form.fields['additional_info'].widget.attrs)
        self.assertContains(response, '追加情報')
        self.assertIn('name="additional_info"', response.content.decode())

    @patch('event.notifications.send_mail')
    def test_submit_with_template_same_as_template_fails(self, mock_send_mail):
        """テンプレートと同一内容で送信するとエラーになる"""
        mock_send_mail.return_value = 1
        self.client.login(username='TestUser', password='testpass123')

        url = reverse('event:lt_application_create', kwargs={'community_pk': self.community_with_template.pk})
        response = self.client.post(url, {
            'event': self.event_with_template.pk,
            'theme': 'Test Theme',
            'speaker': 'TestSpeaker',
            'additional_info': '【発表概要】\n\n【対象者】\n',  # テンプレートと同一
        })

        # フォームエラーで再表示
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'テンプレートの各項目を入力してください')

    @patch('event.notifications.send_mail')
    def test_submit_with_valid_additional_info_succeeds(self, mock_send_mail):
        """有効な追加情報で送信が成功する"""
        mock_send_mail.return_value = 1
        self.client.login(username='TestUser', password='testpass123')

        url = reverse('event:lt_application_create', kwargs={'community_pk': self.community_with_template.pk})
        response = self.client.post(url, {
            'event': self.event_with_template.pk,
            'theme': 'Test Theme',
            'speaker': 'TestSpeaker',
            'additional_info': '【発表概要】VRChatの技術について発表します。【対象者】初心者向け。',
        })

        # リダイレクト確認
        self.assertEqual(response.status_code, 302)

        # EventDetailが作成されたか確認
        event_detail = EventDetail.objects.filter(
            event=self.event_with_template,
            theme='Test Theme'
        ).first()

        self.assertIsNotNone(event_detail)
        self.assertIn('VRChatの技術', event_detail.additional_info)

    @patch('event.notifications.send_mail')
    def test_submit_without_template_no_additional_info(self, mock_send_mail):
        """テンプレートなし集会では追加情報なしで送信できる"""
        mock_send_mail.return_value = 1
        self.client.login(username='TestUser', password='testpass123')

        url = reverse('event:lt_application_create', kwargs={'community_pk': self.community_without_template.pk})
        response = self.client.post(url, {
            'event': self.event_without_template.pk,
            'theme': 'Test Theme',
            'speaker': 'TestSpeaker',
            # additional_info は未入力
        })

        # リダイレクト確認
        self.assertEqual(response.status_code, 302)

        # EventDetailが作成されたか確認
        event_detail = EventDetail.objects.filter(
            event=self.event_without_template,
            theme='Test Theme'
        ).first()

        self.assertIsNotNone(event_detail)
        self.assertEqual(event_detail.additional_info, '')

    @patch('event.notifications.send_mail')
    def test_submit_without_template_with_additional_info_succeeds(self, mock_send_mail):
        """テンプレートなし集会でも追加情報を自由記入できる"""
        mock_send_mail.return_value = 1
        self.client.login(username='TestUser', password='testpass123')

        url = reverse('event:lt_application_create', kwargs={'community_pk': self.community_without_template.pk})
        response = self.client.post(url, {
            'event': self.event_without_template.pk,
            'theme': 'Free Info Theme',
            'speaker': 'TestSpeaker',
            'additional_info': '事前共有したい補足情報です。',
        })

        self.assertEqual(response.status_code, 302)

        event_detail = EventDetail.objects.filter(
            event=self.event_without_template,
            theme='Free Info Theme'
        ).first()

        self.assertIsNotNone(event_detail)
        self.assertEqual(event_detail.additional_info, '事前共有したい補足情報です。')


class LTApplicationReviewAdditionalInfoTest(TweetGenerationPatchMixin, TestCase):
    """LT申請レビュー画面での追加情報表示テスト"""

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
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved',
            lt_application_template='【発表概要】\n',
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

        # 追加情報付き申請
        self.application_with_info = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Test Theme',
            speaker='Test Speaker',
            duration=15,
            start_time=time(22, 0),
            status='pending',
            applicant=self.applicant,
            additional_info='【発表概要】VRChatの技術について発表します。'
        )

        # 追加情報なし申請
        self.application_without_info = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Test Theme 2',
            speaker='Test Speaker 2',
            duration=15,
            start_time=time(22, 15),
            status='pending',
            applicant=self.applicant,
            additional_info=''
        )

    def test_review_page_shows_additional_info(self):
        """レビューページで追加情報が表示される"""
        self.client.login(username='Owner', password='ownerpass123')
        url = reverse('event:lt_application_review', kwargs={'pk': self.application_with_info.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '追加情報')
        self.assertContains(response, 'VRChatの技術について発表します')

    def test_review_page_hides_additional_info_when_empty(self):
        """追加情報が空の場合はセクションが表示されない"""
        self.client.login(username='Owner', password='ownerpass123')
        url = reverse('event:lt_application_review', kwargs={'pk': self.application_without_info.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        # 「追加情報」というラベルが表示されないことを確認
        # ただし、テーブルヘッダーとして「追加情報」がないことを確認
        self.assertNotContains(response, 'VRChatの技術について')
