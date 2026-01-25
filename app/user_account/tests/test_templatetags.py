"""account_tags テンプレートタグのテスト。"""
from django import forms
from django.test import TestCase

from user_account.templatetags.account_tags import add_class, get_field_label


class DummyForm(forms.Form):
    """テスト用のダミーフォーム。"""
    email = forms.EmailField(label="メールアドレス")
    username = forms.CharField(label="ユーザー名")


class AddClassFilterTests(TestCase):
    """add_classフィルターのテスト。"""

    def test_add_class_to_field(self):
        """フォームフィールドにCSSクラスを追加できること。"""
        form = DummyForm()
        field = form['email']

        result = add_class(field, 'form-control')

        self.assertIn('class="form-control"', result)
        self.assertIn('type="email"', result)

    def test_add_class_preserves_existing_class(self):
        """既存のCSSクラスを保持したまま新しいクラスを追加できること。"""
        class FormWithClass(forms.Form):
            email = forms.EmailField(widget=forms.EmailInput(attrs={'class': 'existing-class'}))

        form = FormWithClass()
        field = form['email']

        result = add_class(field, 'form-control')

        self.assertIn('existing-class', result)
        self.assertIn('form-control', result)

    def test_add_class_to_non_field_returns_input(self):
        """フィールドでない値が渡された場合、そのまま返すこと。"""
        result = add_class("not a field", 'form-control')

        self.assertEqual(result, "not a field")


class GetFieldLabelFilterTests(TestCase):
    """get_field_labelフィルターのテスト。"""

    def test_get_field_label_returns_label(self):
        """フィールドのラベルを取得できること。"""
        form = DummyForm()

        result = get_field_label(form, 'email')

        self.assertEqual(result, "メールアドレス")

    def test_get_field_label_with_invalid_field_returns_field_name(self):
        """存在しないフィールド名が渡された場合、フィールド名をそのまま返すこと。"""
        form = DummyForm()

        result = get_field_label(form, 'nonexistent')

        self.assertEqual(result, 'nonexistent')
