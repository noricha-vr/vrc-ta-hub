from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core import mail
from django.utils import timezone
from datetime import date, timedelta, time

from community.models import Community, CommunityMember
from event.models import Event, EventDetail

CustomUser = get_user_model()


class AcceptViewTest(TestCase):
    def setUp(self):
        # 管理者ユーザー（承認権限あり）
        self.admin_user = CustomUser.objects.create_superuser(
            email='admin@example.com',
            password='testpass123',
            user_name='管理者ユーザー'
        )
        self.admin_community = Community.objects.create(
            name='管理者集会',
            status='approved'
        )

        # 未承認ユーザー（承認申請者）
        self.pending_user = CustomUser.objects.create_user(
            email='pending@example.com',
            password='testpass123',
            user_name='未承認ユーザー'
        )
        self.pending_community = Community.objects.create(
            name='未承認集会',
            status='pending'
        )
        # オーナーとしてCommunityMemberを作成
        CommunityMember.objects.create(
            community=self.pending_community,
            user=self.pending_user,
            role=CommunityMember.Role.OWNER
        )

        self.client = Client()

    def test_accept_community_sends_email(self):
        """集会承認時にメールが送信されることをテスト"""
        # 管理者ユーザーでログイン
        self.client.login(username='管理者ユーザー', password='testpass123')

        # 未承認集会を承認（pkパラメータを指定）
        response = self.client.post(
            reverse('community:accept', kwargs={'pk': self.pending_community.pk})
        )

        # リダイレクトを確認
        self.assertRedirects(response, reverse('community:waiting_list'))

        # メールが送信されたことを確認
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]

        # メールの内容を確認
        self.assertEqual(email.subject, f'{self.pending_community.name}が承認されました')
        self.assertEqual(email.to, [self.pending_user.email])
        
        # HTMLメールの内容を確認
        self.assertTrue(hasattr(email, 'alternatives'))
        html_content = email.alternatives[0][0]

        # メールの本文に必要な情報が含まれていることを確認
        self.assertIn(self.pending_community.name, html_content)
        self.assertIn('my_list', html_content)
        self.assertIn('開催日を登録', html_content)
        self.assertIn('<h1>', html_content)  # HTML形式であることを確認




class CommunityDetailViewEventThemeDisplayTest(TestCase):
    """CommunityDetailViewのテーマ表示テスト

    EventDetailのtitleプロパティを使用して表示。
    h1があればh1を表示し、なければthemeを表示する。
    """

    DURATION_MINUTES = 120  # イベント時間（分）

    def setUp(self):
        self.client = Client()

        # テスト用ユーザー
        self.user = CustomUser.objects.create_user(
            email='test@example.com',
            password='testpass123',
            user_name='テストユーザー'
        )

        # テスト用集会
        self.community = Community.objects.create(
            name='テスト集会',
            status='approved',
            frequency='毎週',
            organizers='テスト主催者'
        )
        # オーナーとしてCommunityMemberを作成
        CommunityMember.objects.create(
            community=self.community,
            user=self.user,
            role=CommunityMember.Role.OWNER
        )

        # 将来のイベント（開催日程セクションに表示される）
        future_date = date.today() + timedelta(days=7)
        self.future_event = Event.objects.create(
            community=self.community,
            date=future_date,
            start_time=time(21, 0),
            duration=self.DURATION_MINUTES
        )

        # 過去のイベント（発表履歴セクションに表示される）
        past_date = date.today() - timedelta(days=7)
        self.past_event = Event.objects.create(
            community=self.community,
            date=past_date,
            start_time=time(21, 0),
            duration=self.DURATION_MINUTES
        )

    def test_theme_blog_shows_h1_in_scheduled_events(self):
        """開催日程: BLOGタイプではthemeにh1がコピーされて表示される"""
        # フォーム保存時にh1がthemeにコピーされる仕様のため、
        # テストデータも同様にthemeにh1の値をセット
        EventDetail.objects.create(
            event=self.future_event,
            speaker='発表者A',
            theme='実際のブログタイトル',  # フォームでh1からコピーされた値
            h1='実際のブログタイトル'
        )

        response = self.client.get(
            reverse('community:detail', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 200)
        # themeにコピーされたh1の値が表示される
        self.assertContains(response, '実際のブログタイトル')

    def test_normal_theme_shows_theme_in_scheduled_events(self):
        """開催日程: 通常のthemeはそのまま表示される"""
        # themeが通常の値
        EventDetail.objects.create(
            event=self.future_event,
            speaker='発表者B',
            theme='ゆれをながめる',
            h1=''
        )

        response = self.client.get(
            reverse('community:detail', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ゆれをながめる')

    def test_theme_blog_shows_h1_in_past_events(self):
        """発表履歴: BLOGタイプではthemeにh1がコピーされて表示される"""
        # フォーム保存時にh1がthemeにコピーされる仕様のため、
        # テストデータも同様にthemeにh1の値をセット
        EventDetail.objects.create(
            event=self.past_event,
            speaker='発表者C',
            theme='過去のブログタイトル',  # フォームでh1からコピーされた値
            h1='過去のブログタイトル'
        )

        response = self.client.get(
            reverse('community:detail', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 200)
        # themeにコピーされたh1の値が表示される
        self.assertContains(response, '過去のブログタイトル')

    def test_normal_theme_shows_theme_in_past_events(self):
        """発表履歴: 通常のthemeはそのまま表示される"""
        # themeが通常の値
        EventDetail.objects.create(
            event=self.past_event,
            speaker='発表者D',
            theme='VRの未来を語る',
            h1=''
        )

        response = self.client.get(
            reverse('community:detail', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'VRの未来を語る')

    def test_theme_blog_without_h1_shows_blog(self):
        """themeが「Blog」でh1が空の場合はBlogが表示される"""
        # themeが「Blog」、h1が空（フォールバック）
        EventDetail.objects.create(
            event=self.future_event,
            speaker='発表者E',
            theme='Blog',
            h1=''
        )

        response = self.client.get(
            reverse('community:detail', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 200)
        # h1が空の場合はフォールバックとしてBlogが表示される
        self.assertContains(response, 'Blog')


class CommunityDetailViewLtApplicationSectionTest(TestCase):
    """CommunityDetailViewのLT申請セクション表示テスト"""

    def setUp(self):
        self.client = Client()

        # テスト用ユーザー
        self.user = CustomUser.objects.create_user(
            email='test@example.com',
            password='testpass123',
            user_name='テストユーザー'
        )

        # テスト用集会（承認済み、LT申請受付ON）
        self.community = Community.objects.create(
            name='テスト集会',
            status='approved',
            frequency='毎週',
            organizers='テスト主催者',
            accepts_lt_application=True
        )
        # オーナーとしてCommunityMemberを作成
        CommunityMember.objects.create(
            community=self.community,
            user=self.user,
            role=CommunityMember.Role.OWNER
        )

    def test_lt_section_shown_when_authenticated_and_approved_and_accepts_lt(self):
        """ログイン済み・承認済み・LT受付ONの場合、LT申請セクションが表示される"""
        self.client.login(username='テストユーザー', password='testpass123')
        response = self.client.get(
            reverse('community:detail', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'LT発表を申し込む')
        self.assertContains(response, 'LTを申し込む')

    def test_lt_section_not_shown_when_not_authenticated(self):
        """未ログインの場合、LT申請セクションは表示されない"""
        response = self.client.get(
            reverse('community:detail', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'LT発表を申し込む')

    def test_lt_section_not_shown_when_not_approved(self):
        """未承認集会の場合、LT申請セクションは表示されない"""
        self.community.status = 'pending'
        self.community.save()

        self.client.login(username='テストユーザー', password='testpass123')
        response = self.client.get(
            reverse('community:detail', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'LT発表を申し込む')

    def test_lt_section_not_shown_when_accepts_lt_is_false(self):
        """LT受付OFFの場合、LT申請セクションは表示されない"""
        self.community.accepts_lt_application = False
        self.community.save()

        self.client.login(username='テストユーザー', password='testpass123')
        response = self.client.get(
            reverse('community:detail', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'LT発表を申し込む')
