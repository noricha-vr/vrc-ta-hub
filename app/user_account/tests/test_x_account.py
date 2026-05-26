"""X (Twitter) アカウントフィールドのテスト."""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from user_account.forms import CustomUserChangeForm, normalize_x_account

User = get_user_model()


class NormalizeXAccountTests(TestCase):
    """normalize_x_account のユニットテスト."""

    def test_plain_handle(self):
        self.assertEqual(normalize_x_account('noricha_vr'), 'noricha_vr')

    def test_at_prefix_is_stripped(self):
        self.assertEqual(normalize_x_account('@noricha_vr'), 'noricha_vr')

    def test_x_url_is_stripped(self):
        self.assertEqual(normalize_x_account('https://x.com/noricha_vr'), 'noricha_vr')

    def test_twitter_url_is_stripped(self):
        self.assertEqual(normalize_x_account('https://twitter.com/noricha_vr'), 'noricha_vr')

    def test_x_url_with_path_and_query(self):
        self.assertEqual(normalize_x_account('https://x.com/noricha_vr/status/123?x=1'), 'noricha_vr')

    def test_empty_returns_empty(self):
        self.assertEqual(normalize_x_account(''), '')

    def test_whitespace_is_trimmed(self):
        self.assertEqual(normalize_x_account('  @noricha_vr  '), 'noricha_vr')

    def test_too_long_raises(self):
        # 16文字
        with self.assertRaises(ValidationError):
            normalize_x_account('a' * 16)

    def test_invalid_chars_raises(self):
        with self.assertRaises(ValidationError):
            normalize_x_account('hello world')

    def test_hyphen_raises(self):
        with self.assertRaises(ValidationError):
            normalize_x_account('noricha-vr')


class CustomUserChangeFormTests(TestCase):
    """CustomUserChangeForm の x_account 追加挙動のテスト."""

    def setUp(self):
        self.user = User.objects.create_user(
            user_name='testuser',
            email='test@example.com',
            password='testpass123',
        )

    def test_save_with_plain_handle(self):
        form = CustomUserChangeForm(
            instance=self.user,
            data={
                'user_name': 'testuser',
                'email': 'test@example.com',
                'x_account': 'noricha_vr',
            },
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.user.refresh_from_db()
        self.assertEqual(self.user.x_account, 'noricha_vr')

    def test_save_normalizes_url(self):
        form = CustomUserChangeForm(
            instance=self.user,
            data={
                'user_name': 'testuser',
                'email': 'test@example.com',
                'x_account': 'https://x.com/noricha_vr',
            },
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.user.refresh_from_db()
        self.assertEqual(self.user.x_account, 'noricha_vr')

    def test_empty_is_allowed(self):
        form = CustomUserChangeForm(
            instance=self.user,
            data={
                'user_name': 'testuser',
                'email': 'test@example.com',
                'x_account': '',
            },
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_invalid_handle_rejected(self):
        form = CustomUserChangeForm(
            instance=self.user,
            data={
                'user_name': 'testuser',
                'email': 'test@example.com',
                'x_account': 'invalid handle',
            },
        )
        self.assertFalse(form.is_valid())
        self.assertIn('x_account', form.errors)


class UserUpdateViewTests(TestCase):
    """user_update ビュー経由で x_account を保存・再表示できることを検証."""

    def setUp(self):
        self.user = User.objects.create_user(
            user_name='viewuser',
            email='viewuser@example.com',
            password='pass12345',
        )
        self.client.login(username='viewuser', password='pass12345')

    def test_update_saves_x_account(self):
        url = reverse('account:user_update')
        response = self.client.post(
            url,
            data={
                'user_name': 'viewuser',
                'email': 'viewuser@example.com',
                'x_account': '@noricha_vr',
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.x_account, 'noricha_vr')
