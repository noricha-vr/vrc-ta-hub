"""Twitterテンプレートビューのテスト"""
import datetime

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from community.models import Community, CommunityMember
from twitter.models import TwitterTemplate

CustomUser = get_user_model()


class TwitterTemplateListViewTest(TestCase):
    """TwitterTemplateListViewのテスト"""

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

        # テンプレートを作成
        self.template = TwitterTemplate.objects.create(
            community=self.community,
            name="Test Template",
            template="Test tweet content"
        )

    def test_anonymous_user_redirected_to_login(self):
        """未ログインユーザーはログインページにリダイレクトされる"""
        url = reverse('twitter:template_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/account/login/', response.url)

    def test_owner_can_access_template_list(self):
        """主催者はテンプレート一覧にアクセスできる"""
        self.client.login(username='owner_user', password='testpassword')
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

        url = reverse('twitter:template_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Template")

    def test_staff_can_access_template_list(self):
        """スタッフもテンプレート一覧にアクセスできる"""
        self.client.login(username='staff_user', password='testpassword')
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

        url = reverse('twitter:template_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Template")

    def test_non_member_sees_empty_list(self):
        """メンバーでないユーザーは空のリストを見る"""
        self.client.login(username='other_user', password='testpassword')
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

        url = reverse('twitter:template_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Test Template")

    def test_no_active_community_returns_empty_list(self):
        """active_community_idがない場合は空のリストを返す"""
        self.client.login(username='owner_user', password='testpassword')
        # セッションを空にする
        session = self.client.session
        if 'active_community_id' in session:
            del session['active_community_id']
        session.save()

        url = reverse('twitter:template_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Test Template")


class TwitterTemplateCreateViewTest(TestCase):
    """TwitterTemplateCreateViewのテスト"""

    def setUp(self):
        self.client = Client()
        # ユーザーを作成
        self.owner = CustomUser.objects.create_user(
            user_name="owner_user2",
            email="owner2@example.com",
            password="testpassword"
        )
        self.other_user = CustomUser.objects.create_user(
            user_name="other_user2",
            email="other2@example.com",
            password="testpassword"
        )

        # 集会を作成
        self.community = Community.objects.create(
            custom_user=self.owner,
            name="Test Community 2",
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

    def test_owner_can_create_template(self):
        """主催者はテンプレートを作成できる"""
        self.client.login(username='owner_user2', password='testpassword')
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

        url = reverse('twitter:template_create')
        response = self.client.post(url, {
            'name': 'New Template',
            'template': 'New tweet content'
        })

        # リダイレクトされることを確認
        self.assertEqual(response.status_code, 302)

        # テンプレートが作成されたことを確認
        self.assertTrue(TwitterTemplate.objects.filter(
            community=self.community, name='New Template'
        ).exists())

    def test_non_member_cannot_create_template(self):
        """メンバーでないユーザーはテンプレートを作成できない"""
        self.client.login(username='other_user2', password='testpassword')
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

        url = reverse('twitter:template_create')
        response = self.client.get(url)

        # Forbidden (403) を返す
        self.assertEqual(response.status_code, 403)

    def test_no_active_community_cannot_create(self):
        """active_community_idがない場合はテンプレートを作成できない"""
        self.client.login(username='owner_user2', password='testpassword')
        # セッションを空にする
        session = self.client.session
        if 'active_community_id' in session:
            del session['active_community_id']
        session.save()

        url = reverse('twitter:template_create')
        response = self.client.get(url)

        # Forbidden (403) を返す
        self.assertEqual(response.status_code, 403)


class TwitterTemplateListViewBackwardCompatibilityTest(TestCase):
    """TwitterTemplateListViewの後方互換性テスト"""

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

        # テンプレートを作成
        self.template = TwitterTemplate.objects.create(
            community=self.community,
            name="Legacy Template",
            template="Legacy tweet content"
        )

    def test_legacy_owner_can_access_via_is_manager_fallback(self):
        """
        CommunityMember未作成でも、custom_userはis_manager()のフォールバックで
        アクセス可能（後方互換性を確認）
        """
        self.client.login(username='legacy_owner', password='testpassword')
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

        url = reverse('twitter:template_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Legacy Template")


class TweetEventWithTemplateViewTest(TestCase):
    """TweetEventWithTemplateViewのテスト"""

    def setUp(self):
        from unittest.mock import patch
        self.client = Client()

        # ユーザーを作成
        self.owner = CustomUser.objects.create_user(
            user_name="tweet_owner",
            email="tweet_owner@example.com",
            password="testpassword"
        )

        # 集会を作成
        self.community = Community.objects.create(
            custom_user=self.owner,
            name="Tweet Test Community",
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

        # イベントを作成
        from event.models import Event
        self.event = Event.objects.create(
            community=self.community,
            date=datetime.date(2025, 2, 1),
            start_time=datetime.time(21, 0),
            duration=60
        )

        # テンプレートを作成
        self.template = TwitterTemplate.objects.create(
            community=self.community,
            name="Tweet Preview Template",
            template="Test tweet template"
        )

    def test_tweet_preview_view_returns_intent_url(self):
        """intent URLがコンテキストに含まれることを確認"""
        from unittest.mock import patch

        with patch('twitter.views.generate_tweet') as mock_generate_tweet:
            mock_generate_tweet.return_value = "Test tweet text\nwith newline"

            url = reverse('twitter:tweet_event_with_template', kwargs={
                'event_pk': self.event.pk,
                'template_pk': self.template.pk
            })
            response = self.client.get(url)

            self.assertEqual(response.status_code, 200)
            self.assertIn('intent_url', response.context)
            self.assertIn('raw_tweet_text', response.context)
            self.assertIn('tweet_text', response.context)

            # intent URLが正しい形式であることを確認
            intent_url = response.context['intent_url']
            self.assertTrue(intent_url.startswith('https://twitter.com/intent/tweet?text='))

            # raw_tweet_textが改行を保持していることを確認
            raw_tweet_text = response.context['raw_tweet_text']
            self.assertIn('\n', raw_tweet_text)

            # tweet_textがHTMLの改行に変換されていることを確認
            tweet_text = response.context['tweet_text']
            self.assertIn('<br>', tweet_text)

    def test_tweet_preview_view_template_content(self):
        """テンプレートに適切なコンテンツが含まれることを確認"""
        from unittest.mock import patch

        with patch('twitter.views.generate_tweet') as mock_generate_tweet:
            mock_generate_tweet.return_value = "Test tweet"

            url = reverse('twitter:tweet_event_with_template', kwargs={
                'event_pk': self.event.pk,
                'template_pk': self.template.pk
            })
            response = self.client.get(url)

            self.assertEqual(response.status_code, 200)
            # 新しいUI要素が含まれることを確認
            self.assertContains(response, 'ポストプレビュー')
            self.assertContains(response, 'X (旧Twitter)')
            self.assertContains(response, '予約投稿')
            self.assertContains(response, 'fa-x-twitter')

    def test_tweet_preview_view_empty_tweet_text(self):
        """ツイートテキストが空の場合の処理を確認"""
        from unittest.mock import patch

        with patch('twitter.views.generate_tweet') as mock_generate_tweet:
            mock_generate_tweet.return_value = None

            url = reverse('twitter:tweet_event_with_template', kwargs={
                'event_pk': self.event.pk,
                'template_pk': self.template.pk
            })
            response = self.client.get(url)

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['intent_url'], '')
            self.assertEqual(response.context['tweet_text'], '')
