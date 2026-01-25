"""認証フォームのテスト."""
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from user_account.forms import BootstrapAuthenticationForm

User = get_user_model()


class BootstrapAuthenticationFormTests(TestCase):
    """BootstrapAuthenticationFormのテストクラス."""

    def setUp(self):
        """テスト用のデータを準備."""
        self.factory = RequestFactory()
        self.test_user = User.objects.create_user(
            user_name='test_community',
            email='test@example.com',
            password='testpass123',
        )

    def test_valid_login_with_user_name(self):
        """user_nameフィールドで正常にログインできること."""
        request = self.factory.post('/account/login/')
        form = BootstrapAuthenticationForm(
            request=request,
            data={
                'username': 'test_community',
                'password': 'testpass123',
            }
        )
        self.assertTrue(form.is_valid())
        self.assertEqual(form.get_user(), self.test_user)

    def test_invalid_login_with_wrong_password(self):
        """パスワードが間違っている場合、ログインが失敗すること."""
        request = self.factory.post('/account/login/')
        form = BootstrapAuthenticationForm(
            request=request,
            data={
                'username': 'test_community',
                'password': 'wrongpassword',
            }
        )
        self.assertFalse(form.is_valid())

    def test_invalid_login_with_nonexistent_user(self):
        """存在しないユーザーでログインが失敗すること."""
        request = self.factory.post('/account/login/')
        form = BootstrapAuthenticationForm(
            request=request,
            data={
                'username': 'nonexistent_user',
                'password': 'testpass123',
            }
        )
        self.assertFalse(form.is_valid())

    def test_form_has_bootstrap_class(self):
        """フォームフィールドにBootstrapクラスが適用されていること."""
        request = self.factory.post('/account/login/')
        form = BootstrapAuthenticationForm(request=request)
        for field in form.fields.values():
            self.assertIn('form-control', field.widget.attrs.get('class', ''))

    def test_username_field_has_correct_label(self):
        """usernameフィールドのラベルが「集会名」であること."""
        request = self.factory.post('/account/login/')
        form = BootstrapAuthenticationForm(request=request)
        self.assertEqual(form.fields['username'].label, '集会名')

    def test_inactive_user_cannot_login(self):
        """非アクティブユーザーがログインできないこと."""
        self.test_user.is_active = False
        self.test_user.save()

        request = self.factory.post('/account/login/')
        form = BootstrapAuthenticationForm(
            request=request,
            data={
                'username': 'test_community',
                'password': 'testpass123',
            }
        )
        self.assertFalse(form.is_valid())
