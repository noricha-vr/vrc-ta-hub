"""TweetQueue 管理ビュー（一覧・詳細/編集）のテスト

閲覧（GET）は superuser または所属コミュニティのスタッフ以上が可能。
編集系（POST）は superuser のみが実行できる。
"""

import datetime
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from community.models import Community, CommunityMember
from twitter.models import TweetQueue

CustomUser = get_user_model()


class TweetQueueViewTestBase(TestCase):
    """テスト共通のセットアップ

    Community を status="pending" で作成し、承認シグナルによる
    自動 TweetQueue 生成を防止する。
    """

    def setUp(self):
        self.client = Client()
        self.superuser = CustomUser.objects.create_superuser(
            user_name="admin_user",
            email="admin@example.com",
            password="testpassword",
        )
        self.normal_user = CustomUser.objects.create_user(
            user_name="normal_user",
            email="normal@example.com",
            password="testpassword",
        )
        # status="pending" でシグナルによる TweetQueue 自動生成を回避
        self.community = Community.objects.create(
            name="Queue Test Community",
            start_time=datetime.time(21, 0),
            duration=60,
            weekdays=["Mon"],
            frequency="Weekly",
            organizers="Test Organizer",
            description="Test Description",
            platform="All",
            status="pending",
        )


class TweetQueueListViewTest(TweetQueueViewTestBase):
    """TweetQueueListView のテスト"""

    def test_anonymous_user_redirected(self):
        """未ログインユーザーはログインページにリダイレクトされる"""
        url = reverse('twitter:tweet_queue_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/account/login/', response.url)

    def test_normal_user_forbidden(self):
        """一般ユーザーは 403 を返す"""
        self.client.login(username='normal_user', password='testpassword')
        url = reverse('twitter:tweet_queue_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_superuser_can_access_list(self):
        """スーパーユーザーは一覧にアクセスできる"""
        self.client.login(username='admin_user', password='testpassword')
        TweetQueue.objects.create(
            tweet_type='new_community',
            community=self.community,
            generated_text='Test tweet',
            status='ready',
        )
        url = reverse('twitter:tweet_queue_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Queue Test Community')
        self.assertContains(response, 'Test tweet' if False else 'ポストキュー')

    def test_status_filter(self):
        """ステータスフィルタが正しく動作する"""
        self.client.login(username='admin_user', password='testpassword')
        TweetQueue.objects.create(
            tweet_type='new_community',
            community=self.community,
            generated_text='Ready tweet',
            status='ready',
        )
        TweetQueue.objects.create(
            tweet_type='lt',
            community=self.community,
            generated_text='Posted tweet',
            status='posted',
        )

        # ready でフィルタ
        url = reverse('twitter:tweet_queue_list')
        response = self.client.get(url, {'status': 'ready'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        self.assertEqual(response.context['current_status'], 'ready')

    def test_invalid_status_filter_shows_all(self):
        """無効なステータス値ではフィルタされず全件表示される"""
        self.client.login(username='admin_user', password='testpassword')
        TweetQueue.objects.create(
            tweet_type='new_community',
            community=self.community,
            status='ready',
        )
        TweetQueue.objects.create(
            tweet_type='lt',
            community=self.community,
            status='posted',
        )
        url = reverse('twitter:tweet_queue_list')
        response = self.client.get(url, {'status': 'invalid_status'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page_obj'].paginator.count, 2)

    def test_empty_list(self):
        """キューがない場合は空メッセージが表示される"""
        self.client.login(username='admin_user', password='testpassword')
        url = reverse('twitter:tweet_queue_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'キューがありません')


class TweetQueueDetailViewTest(TweetQueueViewTestBase):
    """TweetQueueDetailView のテスト"""

    def setUp(self):
        super().setUp()
        self.queue_item = TweetQueue.objects.create(
            tweet_type='new_community',
            community=self.community,
            generated_text='Original tweet text',
            status='ready',
        )

    def test_anonymous_user_redirected(self):
        """未ログインユーザーはリダイレクトされる"""
        url = reverse('twitter:tweet_queue_detail', kwargs={'pk': self.queue_item.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_normal_user_forbidden(self):
        """一般ユーザーは 403 を返す"""
        self.client.login(username='normal_user', password='testpassword')
        url = reverse('twitter:tweet_queue_detail', kwargs={'pk': self.queue_item.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_superuser_can_view_detail(self):
        """スーパーユーザーは詳細を表示できる"""
        self.client.login(username='admin_user', password='testpassword')
        url = reverse('twitter:tweet_queue_detail', kwargs={'pk': self.queue_item.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Original tweet text')
        self.assertContains(response, 'Queue Test Community')

    def test_update_generated_text(self):
        """テキストの編集保存ができる"""
        self.client.login(username='admin_user', password='testpassword')
        url = reverse('twitter:tweet_queue_detail', kwargs={'pk': self.queue_item.pk})
        response = self.client.post(url, {
            'action': 'update',
            'generated_text': 'Updated tweet text',
        })
        self.assertEqual(response.status_code, 302)
        self.queue_item.refresh_from_db()
        self.assertEqual(self.queue_item.generated_text, 'Updated tweet text')

    @patch('twitter.views.threading.Thread')
    def test_retry_generation(self, mock_thread_cls):
        """リトライアクションが generation_failed から generating に変更してスレッドを起動する"""
        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread

        self.queue_item.status = 'generation_failed'
        self.queue_item.error_message = 'Previous error'
        self.queue_item.save()

        self.client.login(username='admin_user', password='testpassword')
        url = reverse('twitter:tweet_queue_detail', kwargs={'pk': self.queue_item.pk})
        response = self.client.post(url, {'action': 'retry'})
        self.assertEqual(response.status_code, 302)

        self.queue_item.refresh_from_db()
        self.assertEqual(self.queue_item.status, 'generating')
        self.assertEqual(self.queue_item.error_message, '')

        mock_thread_cls.assert_called_once()
        mock_thread.start.assert_called_once()

    def test_retry_not_allowed_for_ready(self):
        """ready ステータスからはリトライできない"""
        self.client.login(username='admin_user', password='testpassword')
        url = reverse('twitter:tweet_queue_detail', kwargs={'pk': self.queue_item.pk})
        response = self.client.post(url, {'action': 'retry'})
        self.assertEqual(response.status_code, 302)

        self.queue_item.refresh_from_db()
        self.assertEqual(self.queue_item.status, 'ready')

    @patch('twitter.views.post_tweet')
    @patch('twitter.views.upload_media')
    def test_post_now_success(self, mock_upload, mock_post):
        """手動投稿が成功する"""
        mock_post.return_value = {'id': '12345678'}
        mock_upload.return_value = None

        self.client.login(username='admin_user', password='testpassword')
        url = reverse('twitter:tweet_queue_detail', kwargs={'pk': self.queue_item.pk})
        response = self.client.post(url, {'action': 'post_now'})
        self.assertEqual(response.status_code, 302)

        self.queue_item.refresh_from_db()
        self.assertEqual(self.queue_item.status, 'posted')
        self.assertEqual(self.queue_item.tweet_id, '12345678')
        self.assertIsNotNone(self.queue_item.posted_at)

    @patch('twitter.views.post_tweet')
    @patch('twitter.views.upload_media')
    def test_post_now_failure(self, mock_upload, mock_post):
        """手動投稿が失敗した場合に failed になる"""
        mock_post.return_value = None
        mock_upload.return_value = None

        self.client.login(username='admin_user', password='testpassword')
        url = reverse('twitter:tweet_queue_detail', kwargs={'pk': self.queue_item.pk})
        response = self.client.post(url, {'action': 'post_now'})
        self.assertEqual(response.status_code, 302)

        self.queue_item.refresh_from_db()
        self.assertEqual(self.queue_item.status, 'failed')
        self.assertIn('X API', self.queue_item.error_message)

    @patch('twitter.views.post_tweet')
    @patch('twitter.views.upload_media')
    def test_post_now_with_image(self, mock_upload, mock_post):
        """画像付きのツイートが正しくアップロード・投稿される"""
        self.queue_item.image_url = 'https://data.vrc-ta-hub.com/test.png'
        self.queue_item.save()

        mock_upload.return_value = 'media_id_123'
        mock_post.return_value = {'id': '99999'}

        self.client.login(username='admin_user', password='testpassword')
        url = reverse('twitter:tweet_queue_detail', kwargs={'pk': self.queue_item.pk})
        response = self.client.post(url, {'action': 'post_now'})
        self.assertEqual(response.status_code, 302)

        mock_upload.assert_called_once_with('https://data.vrc-ta-hub.com/test.png')
        mock_post.assert_called_once_with(
            self.queue_item.generated_text,
            media_ids=['media_id_123'],
        )

        self.queue_item.refresh_from_db()
        self.assertEqual(self.queue_item.status, 'posted')

    def test_post_now_not_allowed_for_posted(self):
        """posted ステータスからは手動投稿できない"""
        self.queue_item.status = 'posted'
        self.queue_item.save()

        self.client.login(username='admin_user', password='testpassword')
        url = reverse('twitter:tweet_queue_detail', kwargs={'pk': self.queue_item.pk})
        response = self.client.post(url, {'action': 'post_now'})
        self.assertEqual(response.status_code, 302)

        self.queue_item.refresh_from_db()
        self.assertEqual(self.queue_item.status, 'posted')


class TweetQueueStaffAccessTest(TweetQueueViewTestBase):
    """スタッフ・主催者の閲覧権限と他コミュニティへの隔離を確認するテスト。"""

    def setUp(self):
        super().setUp()
        # スタッフ・主催者ユーザーを作成
        self.staff_user = CustomUser.objects.create_user(
            user_name="staff_user",
            email="staff@example.com",
            password="testpassword",
        )
        self.owner_user = CustomUser.objects.create_user(
            user_name="owner_user",
            email="owner@example.com",
            password="testpassword",
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.staff_user,
            role=CommunityMember.Role.STAFF,
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner_user,
            role=CommunityMember.Role.OWNER,
        )

        # 他人のコミュニティと、それに紐づく TweetQueue
        self.other_community = Community.objects.create(
            name="Other Community",
            start_time=datetime.time(20, 0),
            duration=60,
            weekdays=["Tue"],
            frequency="Weekly",
            organizers="Other Organizer",
            description="Other Description",
            platform="All",
            status="pending",
        )
        self.own_queue = TweetQueue.objects.create(
            tweet_type='new_community',
            community=self.community,
            generated_text='Own community tweet',
            status='ready',
        )
        self.other_queue = TweetQueue.objects.create(
            tweet_type='new_community',
            community=self.other_community,
            generated_text='Other community tweet',
            status='ready',
        )

    def test_staff_can_access_list(self):
        """スタッフは一覧にアクセスできる"""
        self.client.login(username='staff_user', password='testpassword')
        url = reverse('twitter:tweet_queue_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_owner_can_access_list(self):
        """主催者は一覧にアクセスできる"""
        self.client.login(username='owner_user', password='testpassword')
        url = reverse('twitter:tweet_queue_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_staff_list_only_shows_own_community(self):
        """スタッフの一覧には自分の集会の TweetQueue だけが表示される"""
        self.client.login(username='staff_user', password='testpassword')
        url = reverse('twitter:tweet_queue_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        queues = list(response.context['page_obj'])
        self.assertIn(self.own_queue, queues)
        self.assertNotIn(self.other_queue, queues)

    def test_superuser_list_shows_all_communities(self):
        """superuser の一覧には全集会の TweetQueue が表示される"""
        self.client.login(username='admin_user', password='testpassword')
        url = reverse('twitter:tweet_queue_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        queues = list(response.context['page_obj'])
        self.assertIn(self.own_queue, queues)
        self.assertIn(self.other_queue, queues)

    def test_staff_can_view_own_detail(self):
        """スタッフは自分の集会の TweetQueue 詳細を閲覧できる"""
        self.client.login(username='staff_user', password='testpassword')
        url = reverse('twitter:tweet_queue_detail', kwargs={'pk': self.own_queue.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Own community tweet')

    def test_staff_cannot_view_other_detail(self):
        """スタッフは他の集会の TweetQueue 詳細にアクセスできない（404）"""
        self.client.login(username='staff_user', password='testpassword')
        url = reverse('twitter:tweet_queue_detail', kwargs={'pk': self.other_queue.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_staff_cannot_post_update(self):
        """スタッフは編集系の POST を実行できない（403）"""
        self.client.login(username='staff_user', password='testpassword')
        url = reverse('twitter:tweet_queue_detail', kwargs={'pk': self.own_queue.pk})
        response = self.client.post(url, {
            'action': 'update',
            'generated_text': 'Hijacked text',
        })
        self.assertEqual(response.status_code, 403)
        self.own_queue.refresh_from_db()
        self.assertEqual(self.own_queue.generated_text, 'Own community tweet')

    def test_staff_cannot_post_delete(self):
        """スタッフは削除系 POST を実行できない（403）"""
        self.client.login(username='staff_user', password='testpassword')
        url = reverse('twitter:tweet_queue_detail', kwargs={'pk': self.own_queue.pk})
        response = self.client.post(url, {'action': 'delete'})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(TweetQueue.objects.filter(pk=self.own_queue.pk).exists())

    def test_user_without_membership_forbidden(self):
        """CommunityMember を持たない一般ユーザーは 403"""
        self.client.login(username='normal_user', password='testpassword')
        url = reverse('twitter:tweet_queue_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)
