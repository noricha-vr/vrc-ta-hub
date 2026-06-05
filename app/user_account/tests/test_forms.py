"""認証フォームのテスト."""
from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from user_account.forms import (
    BootstrapAuthenticationForm,
    CustomSocialSignupForm,
    CustomUserCreationForm,
    CustomUserChangeForm,
)

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
        for name, field in form.fields.items():
            css_class = field.widget.attrs.get('class', '')
            if name == 'remember':
                # チェックボックスはform-check-inputを使用
                self.assertIn('form-check-input', css_class)
            else:
                self.assertIn('form-control', css_class)

    def test_username_field_has_correct_label(self):
        """usernameフィールドのラベルが「ユーザー名」であること."""
        request = self.factory.post('/account/login/')
        form = BootstrapAuthenticationForm(request=request)
        self.assertEqual(form.fields['username'].label, 'ユーザー名')

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

    def test_remember_field_exists(self):
        """rememberフィールドが存在すること."""
        request = self.factory.post('/account/login/')
        form = BootstrapAuthenticationForm(request=request)
        self.assertIn('remember', form.fields)
        self.assertEqual(form.fields['remember'].label, 'ログインしたままにする')
        self.assertFalse(form.fields['remember'].required)


class CustomSocialSignupFormTests(TestCase):
    """CustomSocialSignupFormのテストクラス."""

    def setUp(self):
        """テスト用のモックオブジェクトを準備."""
        # SocialLoginのモック
        self.mock_sociallogin = MagicMock()
        self.mock_sociallogin.user = MagicMock()
        self.mock_sociallogin.user.email = 'test@example.com'

    def test_form_has_bootstrap_class(self):
        """フォームフィールドにBootstrapのform-controlクラスが適用されていること."""
        form = CustomSocialSignupForm(sociallogin=self.mock_sociallogin)
        for field_name, field in form.fields.items():
            self.assertIn(
                'form-control',
                field.widget.attrs.get('class', ''),
                f'{field_name}フィールドにform-controlクラスがありません'
            )

    def test_email_field_exists(self):
        """emailフィールドが存在すること."""
        form = CustomSocialSignupForm(sociallogin=self.mock_sociallogin)
        self.assertIn('email', form.fields)

    def test_email_is_required(self):
        """メールアドレスが必須であること."""
        form = CustomSocialSignupForm(
            sociallogin=self.mock_sociallogin,
            data={'email': ''}
        )
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)

    def test_email_label_is_correct(self):
        """emailフィールドのラベルが「メールアドレス」であること."""
        form = CustomSocialSignupForm(sociallogin=self.mock_sociallogin)
        self.assertEqual(form.fields['email'].label, 'メールアドレス')

    def test_user_name_field_exists(self):
        """user_nameフィールドが存在すること."""
        form = CustomSocialSignupForm(sociallogin=self.mock_sociallogin)
        self.assertIn('user_name', form.fields)

    def test_user_name_is_required(self):
        """ユーザー名が必須であること."""
        form = CustomSocialSignupForm(
            sociallogin=self.mock_sociallogin,
            data={'email': 'test@example.com', 'user_name': ''}
        )
        self.assertFalse(form.is_valid())
        self.assertIn('user_name', form.errors)

    def test_user_name_label_is_correct(self):
        """user_nameフィールドのラベルが「ログインユーザー名」であること."""
        form = CustomSocialSignupForm(sociallogin=self.mock_sociallogin)
        self.assertEqual(form.fields['user_name'].label, 'ログインユーザー名')

    def test_user_name_max_length(self):
        """user_nameフィールドの最大文字数が150であること."""
        form = CustomSocialSignupForm(sociallogin=self.mock_sociallogin)
        self.assertEqual(form.fields['user_name'].max_length, 150)

    def test_field_order_is_user_name_then_email(self):
        """フィールド順序がuser_name、emailの順であること."""
        form = CustomSocialSignupForm(sociallogin=self.mock_sociallogin)
        field_names = list(form.fields.keys())
        user_name_index = field_names.index('user_name')
        email_index = field_names.index('email')
        self.assertLess(user_name_index, email_index)

    def test_discord_username_placeholder(self):
        """Discordユーザー名がプレースホルダーに設定されること."""
        self.mock_sociallogin.account = MagicMock()
        self.mock_sociallogin.account.extra_data = {'username': 'discord_user'}
        form = CustomSocialSignupForm(sociallogin=self.mock_sociallogin)
        self.assertEqual(
            form.fields['user_name'].widget.attrs.get('placeholder'),
            'discord_user'
        )

    def test_email_duplicate_validation(self):
        """既存メールアドレスでエラーが発生すること."""
        User.objects.create_user(
            user_name='existing_user',
            email='existing@example.com',
            password='testpass123',
        )
        form = CustomSocialSignupForm(
            sociallogin=self.mock_sociallogin,
            data={'email': 'existing@example.com', 'user_name': 'new_user'}
        )
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)
        self.assertIn('このメールアドレスは既に登録されています', form.errors['email'][0])

    def test_email_duplicate_validation_case_insensitive(self):
        """大文字小文字を区別せずに既存メールアドレスをチェックすること."""
        User.objects.create_user(
            user_name='case_test_user',
            email='Test@Example.com',
            password='testpass123',
        )
        # 大文字小文字が異なる同じメールアドレス
        form = CustomSocialSignupForm(
            sociallogin=self.mock_sociallogin,
            data={'email': 'test@example.com', 'user_name': 'new_user'}
        )
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)
        self.assertIn('このメールアドレスは既に登録されています', form.errors['email'][0])

    def test_email_duplicate_validation_case_insensitive_uppercase(self):
        """大文字で登録されたメールに対して小文字でもエラーが発生すること."""
        User.objects.create_user(
            user_name='uppercase_user',
            email='user@example.com',
            password='testpass123',
        )
        # 全て大文字で試行
        form = CustomSocialSignupForm(
            sociallogin=self.mock_sociallogin,
            data={'email': 'USER@EXAMPLE.COM', 'user_name': 'new_user'}
        )
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)
        self.assertIn('このメールアドレスは既に登録されています', form.errors['email'][0])

    def test_user_name_duplicate_validation(self):
        """既存ユーザー名でエラーが発生すること."""
        User.objects.create_user(
            user_name='existing_user',
            email='existing@example.com',
            password='testpass123',
        )
        form = CustomSocialSignupForm(
            sociallogin=self.mock_sociallogin,
            data={'email': 'new@example.com', 'user_name': 'existing_user'}
        )
        self.assertFalse(form.is_valid())
        self.assertIn('user_name', form.errors)
        self.assertIn('このユーザー名は既に使用されています', form.errors['user_name'][0])


class CustomUserChangeFormTests(TestCase):
    """CustomUserChangeFormのテストクラス."""

    def setUp(self):
        """テスト用のユーザーを準備."""
        self.test_user = User.objects.create_user(
            user_name='test_user',
            email='test@example.com',
            password='testpass123',
        )

    def test_user_name_label_is_correct(self):
        """user_nameフィールドのラベルが「ログインユーザー名」であること."""
        form = CustomUserChangeForm(instance=self.test_user)
        self.assertEqual(form.fields['user_name'].label, 'ログインユーザー名')

    def test_display_name_label_is_correct(self):
        """display_nameフィールドのラベルが「表示名」であること."""
        form = CustomUserChangeForm(instance=self.test_user)
        self.assertEqual(form.fields['display_name'].label, '表示名')

    def test_display_name_help_text_explains_public_display(self):
        """display_nameの説明が公開表示と重複許可を伝えること."""
        form = CustomUserChangeForm(instance=self.test_user)
        self.assertEqual(
            form.fields['display_name'].help_text,
            'VRChat内の名前や発表者名として表示されます。同じ表示名を複数ユーザーが使用できます。',
        )

    def test_user_name_help_text_explains_login_identifier(self):
        """user_nameの説明がログイン用の一意な識別子であることを伝えること."""
        form = CustomUserChangeForm(instance=self.test_user)
        self.assertEqual(
            form.fields['user_name'].help_text,
            'ログインと内部識別に使用する一意のユーザー名です。通常は変更不要です。',
        )

    def test_form_has_bootstrap_class(self):
        """フォームフィールドにBootstrapのform-controlクラスが適用されていること."""
        form = CustomUserChangeForm(instance=self.test_user)
        for field_name, field in form.fields.items():
            self.assertIn(
                'form-control',
                field.widget.attrs.get('class', ''),
                f'{field_name}フィールドにform-controlクラスがありません'
            )

    def test_form_fields(self):
        """フォームにdisplay_name、user_name、emailフィールドが存在すること."""
        form = CustomUserChangeForm(instance=self.test_user)
        self.assertIn('display_name', form.fields)
        self.assertIn('user_name', form.fields)
        self.assertIn('email', form.fields)

    def test_display_name_allows_duplicates(self):
        """複数ユーザーが同じdisplay_nameを設定できること."""
        User.objects.create_user(
            user_name='other_user',
            email='other@example.com',
            password='testpass123',
            display_name='同じ表示名',
        )
        form = CustomUserChangeForm(
            instance=self.test_user,
            data={
                'display_name': '同じ表示名',
                'user_name': self.test_user.user_name,
                'email': self.test_user.email,
                'x_account': '',
                'vrchat_user_id': '',
            },
        )
        self.assertTrue(form.is_valid(), form.errors)


class CustomUserCreationFormTests(TestCase):
    """CustomUserCreationFormのX表記テスト."""

    def test_x_labels_placeholders_and_help_text(self):
        """X関連フィールドの表記が更新されていること."""
        form = CustomUserCreationForm()

        self.assertEqual(form.fields['sns_url'].label, 'XアカウントURL')
        self.assertEqual(form.fields['twitter_hashtag'].label, 'Xハッシュタグ')
        self.assertEqual(form.fields['sns_url'].widget.attrs.get('placeholder'), 'https://x.com/XXXXX')
        self.assertEqual(form.fields['twitter_hashtag'].widget.attrs.get('placeholder'), '#VRChat')
        self.assertEqual(form.fields['sns_url'].help_text, 'X以外のSNSのURLも可')
