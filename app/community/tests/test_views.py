from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core import mail
from datetime import date, timedelta, time
from unittest.mock import patch

from community.models import Community, CommunityMember
from community.views.public import CommunityListView
from event.models import Event, EventDetail

CustomUser = get_user_model()


class TestCommunityListViewPagination(TestCase):
    def setUp(self):
        self.client = Client()
        self.community = Community.objects.create(
            name='一覧表示テスト集会',
            status='approved',
            frequency='毎週',
            organizers='テスト主催者',
            poster_image='poster/test.jpg',
        )

    def test_invalid_page_query_redirects_to_first_page(self):
        response = self.client.get(
            reverse('community:list'),
            {'page': "1'", 'query': '一覧'},
        )

        self.assertRedirects(
            response,
            "/community/list/?page=1&query=%E4%B8%80%E8%A6%A7",
        )

    def test_out_of_range_page_redirects_to_first_page(self):
        response = self.client.get(reverse('community:list'), {'page': '999'})

        self.assertRedirects(response, '/community/list/?page=1')

    def test_last_page_query_is_accepted(self):
        response = self.client.get(reverse('community:list'), {'page': 'last'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.community.name)

    def test_filtered_page_builds_queryset_once(self):
        for index in range(20):
            Community.objects.create(
                name=f'土曜集会{index}',
                status='approved',
                frequency='毎週',
                organizers='テスト主催者',
                poster_image=f'poster/sat-{index}.jpg',
                weekdays=['Sat'],
            )

        original_get_queryset = CommunityListView.get_queryset
        call_count = 0

        def counting_get_queryset(view):
            nonlocal call_count
            call_count += 1
            return original_get_queryset(view)

        url = (
            f"{reverse('community:list')}"
            "?page=2"
            "&query=土曜"
            "&amp;amp%3Bamp%3Btags=academic"
            "&amp%3Bweekdays=Other"
        )
        with patch.object(
            CommunityListView,
            'get_queryset',
            autospec=True,
            side_effect=counting_get_queryset,
        ):
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '件数：20')
        self.assertEqual(call_count, 1)


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

    def test_scheduled_events_section_hidden_when_empty(self):
        """開催日程が0件の場合、開催日程セクションが表示されない"""
        # future_event を削除して開催予定を空にする
        self.future_event.delete()
        response = self.client.get(
            reverse('community:detail', kwargs={'pk': self.community.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'の開催日程')

    def test_past_events_section_hidden_when_empty(self):
        """発表履歴が0件の場合、発表履歴セクションが表示されない"""
        # past_event に EventDetail を作成しない状態でアクセス
        response = self.client.get(
            reverse('community:detail', kwargs={'pk': self.community.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'の発表履歴')

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


class CommunityDetailViewBlogSpecialSectionTest(TestCase):
    """コミュニティ詳細ページのBLOG/SPECIAL記事セクション表示テスト"""

    DURATION_MINUTES = 120

    def setUp(self):
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            email='blogtest@example.com',
            password='testpass123',
            user_name='ブログテストユーザー'
        )
        self.community = Community.objects.create(
            name='テスト集会BS',
            status='approved',
            frequency='毎週',
            organizers='テスト主催者'
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.user,
            role=CommunityMember.Role.OWNER
        )
        self.past_event = Event.objects.create(
            community=self.community,
            date=date.today() - timedelta(days=7),
            start_time=time(21, 0),
            duration=self.DURATION_MINUTES
        )
        self.url = reverse('community:detail', kwargs={'pk': self.community.pk})

    def test_blog_special_section_shown_when_exists(self):
        """BLOG/SPECIALがある場合、記事・特別企画セクションが表示される"""
        EventDetail.objects.create(
            event=self.past_event,
            detail_type='BLOG',
            status='approved',
            h1='テストブログ記事',
            theme='テストブログ記事',
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '記事・特別企画')
        self.assertContains(response, 'テストブログ記事')
        self.assertContains(response, 'badge bg-info')

    def test_special_type_shows_warning_badge(self):
        """SPECIALタイプは特別企画バッジで表示される"""
        EventDetail.objects.create(
            event=self.past_event,
            detail_type='SPECIAL',
            status='approved',
            h1='テスト特別企画',
            theme='Special Event',
        )
        response = self.client.get(self.url)
        self.assertContains(response, 'テスト特別企画')
        self.assertContains(response, 'badge bg-warning')

    def test_blog_special_section_hidden_when_none(self):
        """BLOG/SPECIALがない場合、セクションが表示されない"""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '記事・特別企画')

    def test_unapproved_blog_not_shown(self):
        """未承認のBLOG/SPECIALは表示されない"""
        EventDetail.objects.create(
            event=self.past_event,
            detail_type='BLOG',
            status='pending',
            h1='未承認ブログ',
            theme='未承認ブログ',
        )
        response = self.client.get(self.url)
        self.assertNotContains(response, '未承認ブログ')
        self.assertNotContains(response, '記事・特別企画')

    def test_blog_excluded_from_lt_table(self):
        """BLOGタイプはLT向けの発表履歴テーブルに表示されない"""
        EventDetail.objects.create(
            event=self.past_event,
            detail_type='BLOG',
            status='approved',
            h1='ブログ記事タイトル',
            theme='ブログ記事タイトル',
        )
        response = self.client.get(self.url)
        # past_events コンテキスト内にBLOGが含まれない
        past_events = response.context['past_events']
        for event_dict in past_events:
            for detail in event_dict['details']:
                self.assertNotEqual(detail.detail_type, 'BLOG')


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
        """未承認集会の場合、詳細ページ自体が閲覧できない（superuserのみ閲覧可）"""
        self.community.status = 'pending'
        self.community.save()

        self.client.login(username='テストユーザー', password='testpass123')
        response = self.client.get(
            reverse('community:detail', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, 'LT発表を申し込む', status_code=404)

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


class CommunityUpdateViewPromotionBannerTest(TestCase):
    """CommunityUpdateViewのプロモーションバナー表示テスト"""

    def setUp(self):
        self.client = Client()

        # テスト用ユーザー
        self.user = CustomUser.objects.create_user(
            email='test@example.com',
            password='testpass123',
            user_name='テストユーザー'
        )

        # テスト用集会（承認済み）
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

    def test_promotion_banner_not_displayed_on_update_page(self):
        """集会編集ページにプロモーションバナーを表示しないこと"""
        self.client.login(username='テストユーザー', password='testpass123')

        # アクティブ集会を設定するためセッションを設定
        session = self.client.session
        session['active_community_id'] = self.community.pk
        session.save()

        response = self.client.get(
            reverse('community:update')
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Hubを広めるご協力のお願い')
        self.assertNotContains(response, 'ワールドへのポスター掲示やDiscordでの紹介')
        self.assertNotContains(response, '今後表示しない')


class CommunityDetailAdminCleanupButtonTest(TestCase):
    """コミュニティ詳細の管理者ワンクリック修復ボタン表示テスト"""

    def setUp(self):
        self.client = Client()

        self.admin = CustomUser.objects.create_superuser(
            email='admin-cleanup@example.com',
            password='testpass123',
            user_name='管理者メンテ'
        )
        self.normal_user = CustomUser.objects.create_user(
            email='normal@example.com',
            password='testpass123',
            user_name='一般ユーザー'
        )
        self.community = Community.objects.create(
            name='ボタン表示テスト集会',
            status='approved',
            frequency='毎週',
            organizers='テスト主催'
        )

    def test_superuser_sees_admin_cleanup_button(self):
        self.client.login(username='管理者メンテ', password='testpass123')
        response = self.client.get(reverse('community:detail', kwargs={'pk': self.community.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '管理者メンテナンス（ワンクリック）')
        self.assertContains(response, 'adminCleanupModal')
        self.assertContains(response, '管理者ワンクリック修復を実行')

    def test_non_superuser_cannot_see_admin_cleanup_button(self):
        self.client.login(username='一般ユーザー', password='testpass123')
        response = self.client.get(reverse('community:detail', kwargs={'pk': self.community.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '管理者メンテナンス（ワンクリック）')
        self.assertNotContains(response, 'adminCleanupModal')
