"""カスタムユーザーモデルの認証識別子に関するテスト。"""

from django.contrib.auth import authenticate, get_user_model
from django.db import IntegrityError
from django.test import TestCase

User = get_user_model()


class CustomUserAuthenticationIdentifierTests(TestCase):
    """メールアドレスを認証識別子として扱うことを検証する。"""

    def test_email_is_username_field_and_user_name_is_required_field(self):
        """メールアドレスが認証識別子であることを確認する。"""
        self.assertEqual(User.USERNAME_FIELD, 'email')
        self.assertEqual(User.REQUIRED_FIELDS, ['user_name'])

    def test_create_user_rejects_missing_email(self):
        """メールアドレスなしのユーザー作成を拒否することを確認する。"""
        with self.assertRaisesMessage(ValueError, 'メールアドレスは必須項目です。'):
            User.objects.create_user(email=None, user_name='missing_email_user')

    def test_create_user_normalizes_email_to_lowercase(self):
        """作成時にメールアドレスを小文字で保存することを確認する。"""
        user = User.objects.create_user(
            email='Foo@Example.com',
            user_name='uppercase_email_user',
            password='testpass123',
        )

        self.assertEqual(user.email, 'foo@example.com')

    def test_create_user_rejects_case_variant_email_duplicate(self):
        """大小文字違いのメールアドレス重複をDB制約で拒否することを確認する。"""
        User.objects.create_user(
            email='Foo@Example.com',
            user_name='first_email_user',
            password='testpass123',
        )

        with self.assertRaises(IntegrityError):
            User.objects.create_user(
                email='foo@example.com',
                user_name='duplicate_email_user',
                password='testpass123',
            )

    def test_authenticate_accepts_case_variant_email(self):
        """実際の認証バックエンドが大小文字違いのメールを認証することを確認する。"""
        user = User.objects.create_user(
            email='Foo@Example.com',
            user_name='case_login_user',
            password='testpass123',
        )

        authenticated_user = authenticate(
            username='FOO@EXAMPLE.COM',
            password='testpass123',
        )

        self.assertEqual(authenticated_user, user)

    def test_authenticate_rejects_user_name(self):
        """user_name では認証できないことを確認する。"""
        User.objects.create_user(
            email='user-name-login@example.com',
            user_name='not_an_email_login',
            password='testpass123',
        )

        self.assertIsNone(authenticate(username='not_an_email_login', password='testpass123'))
