"""account_tags テンプレートタグのテスト。"""
from django import forms
from django.test import TestCase

from user_account.templatetags.account_tags import (
    add_class,
    get_field_label,
    has_email_duplicate_error,
    get_other_errors,
    EMAIL_DUPLICATE_ERROR_KEYWORD,
)


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


class HasEmailDuplicateErrorFilterTests(TestCase):
    """has_email_duplicate_errorフィルターのテスト。"""

    def test_returns_true_when_email_duplicate_error_exists(self):
        """メールアドレス重複エラーがある場合、Trueを返すこと。"""
        form = DummyForm(data={'email': '', 'username': ''})
        form.is_valid()
        form._errors['email'] = [EMAIL_DUPLICATE_ERROR_KEYWORD + '。']

        result = has_email_duplicate_error(form)

        self.assertTrue(result)

    def test_returns_false_when_no_email_duplicate_error(self):
        """メールアドレス重複エラーがない場合、Falseを返すこと。"""
        form = DummyForm(data={'email': '', 'username': ''})
        form.is_valid()

        result = has_email_duplicate_error(form)

        self.assertFalse(result)

    def test_returns_false_when_no_errors(self):
        """エラーがない場合、Falseを返すこと。"""
        form = DummyForm(data={'email': 'test@example.com', 'username': 'testuser'})
        form.is_valid()

        result = has_email_duplicate_error(form)

        self.assertFalse(result)

    def test_returns_false_for_non_form_object(self):
        """フォーム以外のオブジェクトが渡された場合、Falseを返すこと。"""
        result = has_email_duplicate_error("not a form")

        self.assertFalse(result)


class GetOtherErrorsFilterTests(TestCase):
    """get_other_errorsフィルターのテスト。"""

    def test_returns_non_email_duplicate_errors(self):
        """メールアドレス重複エラー以外のエラーを返すこと。"""
        form = DummyForm(data={'email': '', 'username': ''})
        form.is_valid()

        result = get_other_errors(form)

        self.assertGreater(len(result), 0)
        for error in result:
            self.assertNotIn(EMAIL_DUPLICATE_ERROR_KEYWORD, error)

    def test_excludes_email_duplicate_error(self):
        """メールアドレス重複エラーを除外すること。"""
        form = DummyForm(data={'email': 'invalid', 'username': ''})
        form.is_valid()
        form._errors['email'] = [EMAIL_DUPLICATE_ERROR_KEYWORD + '。', '無効なメールアドレスです。']

        result = get_other_errors(form)

        self.assertIn('無効なメールアドレスです。', result)
        for error in result:
            self.assertNotIn(EMAIL_DUPLICATE_ERROR_KEYWORD, error)

    def test_returns_empty_list_when_only_email_duplicate_error(self):
        """メールアドレス重複エラーのみの場合、空リストを返すこと。"""
        form = DummyForm(data={'email': 'test@example.com', 'username': 'testuser'})
        form.is_valid()
        form._errors = {'email': [EMAIL_DUPLICATE_ERROR_KEYWORD + '。']}

        result = get_other_errors(form)

        self.assertEqual(result, [])

    def test_returns_empty_list_when_no_errors(self):
        """エラーがない場合、空リストを返すこと。"""
        form = DummyForm(data={'email': 'test@example.com', 'username': 'testuser'})
        form.is_valid()

        result = get_other_errors(form)

        self.assertEqual(result, [])

    def test_returns_empty_list_for_non_form_object(self):
        """フォーム以外のオブジェクトが渡された場合、空リストを返すこと。"""
        result = get_other_errors("not a form")

        self.assertEqual(result, [])
