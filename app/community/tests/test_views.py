from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core import mail

from community.models import Community

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
