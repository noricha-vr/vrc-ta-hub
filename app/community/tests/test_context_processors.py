from datetime import date, timedelta

from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware

from community.models import Community, CommunityMember
from community.context_processors import active_community

CustomUser = get_user_model()


class ActiveCommunityContextProcessorTest(TestCase):
    """active_community コンテキストプロセッサのテスト"""

    def setUp(self):
        self.factory = RequestFactory()

        # テスト用ユーザーを作成
        self.user1 = CustomUser.objects.create_user(
            email='user1@example.com',
            password='testpass123',
            user_name='ユーザー1'
        )
        self.user2 = CustomUser.objects.create_user(
            email='user2@example.com',
            password='testpass123',
            user_name='ユーザー2'
        )

        # テスト用集会を作成
        self.community1 = Community.objects.create(
            name='集会1',
            status='approved',
            frequency='毎週'
        )
        self.community2 = Community.objects.create(
            name='集会2',
            status='approved',
            frequency='隔週'
        )

        # CommunityMemberを作成
        CommunityMember.objects.create(
            community=self.community1,
            user=self.user1,
            role=CommunityMember.Role.OWNER
        )
        CommunityMember.objects.create(
            community=self.community2,
            user=self.user1,
            role=CommunityMember.Role.STAFF
        )

    def _add_session_to_request(self, request):
        """リクエストにセッションを追加するヘルパー"""
        middleware = SessionMiddleware(lambda x: None)
        middleware.process_request(request)
        request.session.save()

    def test_anonymous_user_returns_empty(self):
        """未認証ユーザーは空の辞書を返す"""
        from django.contrib.auth.models import AnonymousUser
        request = self.factory.get('/')
        request.user = AnonymousUser()
        self._add_session_to_request(request)

        result = active_community(request)

        self.assertEqual(result, {'active_membership': None})

    def test_user_without_memberships_returns_empty(self):
        """集会メンバーシップがないユーザーは空を返す"""
        request = self.factory.get('/')
        request.user = self.user2  # メンバーシップなし
        self._add_session_to_request(request)

        result = active_community(request)

        self.assertEqual(result['user_communities'], [])
        self.assertIsNone(result['active_community'])

    def test_user_with_memberships_returns_first_community(self):
        """セッションにactive_community_idがない場合、最初の集会を返す"""
        request = self.factory.get('/')
        request.user = self.user1
        self._add_session_to_request(request)

        result = active_community(request)

        self.assertIn(result['active_community'], [self.community1, self.community2])
        self.assertIsNotNone(result['active_membership'])
        self.assertEqual(len(result['user_communities']), 2)
        # セッションに設定されることを確認
        self.assertIn('active_community_id', request.session)

    def test_respects_session_active_community_id(self):
        """セッションのactive_community_idを尊重する"""
        request = self.factory.get('/')
        request.user = self.user1
        self._add_session_to_request(request)
        request.session['active_community_id'] = self.community2.id

        result = active_community(request)

        self.assertEqual(result['active_community'], self.community2)
        self.assertEqual(result['active_membership'].community, self.community2)

    def test_ignores_invalid_session_community_id(self):
        """無効なactive_community_idは無視される"""
        request = self.factory.get('/')
        request.user = self.user1
        self._add_session_to_request(request)
        request.session['active_community_id'] = 99999  # 存在しないID

        result = active_community(request)

        # 無効なIDの場合、最初の集会にフォールバック
        self.assertIsNotNone(result['active_community'])
        self.assertIn(result['active_community'], [self.community1, self.community2])

    def test_ended_active_community_auto_switches(self):
        """アクティブな集会が終了している場合、次のアクティブな集会に切り替わる"""
        self.community1.end_at = date.today() - timedelta(days=1)
        self.community1.save(update_fields=['end_at'])

        request = self.factory.get('/')
        request.user = self.user1
        self._add_session_to_request(request)
        request.session['active_community_id'] = self.community1.id

        result = active_community(request)

        # 終了した集会1ではなく、アクティブな集会2に切り替わる
        self.assertEqual(result['active_community'], self.community2)
        self.assertEqual(request.session['active_community_id'], self.community2.id)

    def test_all_communities_ended_returns_none(self):
        """全集会が終了している場合、active_communityはNone"""
        self.community1.end_at = date.today() - timedelta(days=1)
        self.community1.save(update_fields=['end_at'])
        self.community2.end_at = date.today() - timedelta(days=1)
        self.community2.save(update_fields=['end_at'])

        request = self.factory.get('/')
        request.user = self.user1
        self._add_session_to_request(request)

        result = active_community(request)

        self.assertIsNone(result['active_community'])
        self.assertIsNone(result['active_membership'])
        # user_communitiesは全集会を返す（設定ページで表示するため）
        self.assertEqual(len(result['user_communities']), 2)

    def test_default_fallback_skips_ended_community(self):
        """セッションにIDがない場合、終了した集会をスキップしてアクティブな集会を選ぶ"""
        self.community1.end_at = date.today() - timedelta(days=1)
        self.community1.save(update_fields=['end_at'])

        request = self.factory.get('/')
        request.user = self.user1
        self._add_session_to_request(request)

        result = active_community(request)

        self.assertEqual(result['active_community'], self.community2)
