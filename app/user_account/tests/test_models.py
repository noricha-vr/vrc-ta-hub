"""カスタムユーザーモデルの認証識別子に関するテスト。"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from user_account.backends import EmailBackend

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

    def test_email_backend_rejects_case_insensitive_duplicate_email(self):
        """大小文字違いの重複メールアドレスを認証しないことを確認する。"""
        with patch.object(
            User._default_manager,
            'get',
            side_effect=User.MultipleObjectsReturned,
        ):
            user = EmailBackend().authenticate(
                request=None,
                username='duplicate@example.com',
                password='testpass123',
            )

        self.assertIsNone(user)
