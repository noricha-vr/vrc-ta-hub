from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core import mail

from community.models import Community, CommunityMember

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
            custom_user=self.admin_user,
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
            custom_user=self.pending_user,
            status='pending'
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


class CommunityUpdateViewBackwardCompatibilityTest(TestCase):
    """CommunityUpdateViewの後方互換性テスト"""

    def setUp(self):
        self.client = Client()

        # レガシーオーナー（CommunityMemberなしで集会を持つ）
        self.legacy_owner = CustomUser.objects.create_user(
            email='legacy@example.com',
            password='testpass123',
            user_name='レガシーオーナー'
        )

        # レガシー集会（CommunityMemberなし）
        self.legacy_community = Community.objects.create(
            name='レガシー集会',
            custom_user=self.legacy_owner,
            status='approved',
            frequency='毎週',
            organizers='レガシー主催者'
        )
        # 意図的にCommunityMemberを作成しない

        # その他のユーザー
        self.other_user = CustomUser.objects.create_user(
            email='other@example.com',
            password='testpass123',
            user_name='その他ユーザー'
        )

    def test_legacy_owner_can_access_update_page(self):
        """CommunityMember未作成でもcustom_userは集会編集ページにアクセスできる"""
        # CommunityMemberが存在しないことを確認
        self.assertFalse(
            CommunityMember.objects.filter(community=self.legacy_community).exists()
        )

        self.client.login(username='レガシーオーナー', password='testpass123')
        url = reverse('community:update')
        response = self.client.get(url)

        # アクセスできることを確認（200または302でリダイレクト）
        self.assertIn(response.status_code, [200, 302])

        # 200の場合は正常に表示される
        if response.status_code == 200:
            self.assertContains(response, 'レガシー集会')

    def test_other_user_cannot_access_legacy_community_update(self):
        """custom_user以外のユーザーはレガシー集会を編集できない"""
        self.client.login(username='その他ユーザー', password='testpass123')
        url = reverse('community:update')
        response = self.client.get(url)

        # 集会情報が表示されない（403またはリダイレクト）
        # get_objectがNoneを返すため、test_funcがFalseを返してアクセス拒否される
        self.assertNotEqual(response.status_code, 200)

    def test_test_func_allows_custom_user(self):
        """test_funcがcustom_userに対してTrueを返すことを確認"""
        # can_editが正しく動作していることを直接テスト
        self.assertTrue(self.legacy_community.can_edit(self.legacy_owner))
        self.assertFalse(self.legacy_community.can_edit(self.other_user))
