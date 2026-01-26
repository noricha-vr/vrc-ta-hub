from django import template

register = template.Library()


@register.filter
def get_field_label(form, field_name):
    """フォームからフィールドのラベルを取得する。"""
    try:
        return form.fields[field_name].label
    except (KeyError, AttributeError):
        return field_name


@register.filter
def add_class(field, css_class):
    """フォームフィールドにCSSクラスを追加する。

    使用例:
        {{ field|add_class:"form-control" }}
    """
    if hasattr(field, 'as_widget'):
        existing_classes = field.field.widget.attrs.get('class', '')
        if existing_classes:
            css_class = f"{existing_classes} {css_class}"
        return field.as_widget(attrs={'class': css_class})
    return field


# メール重複エラーを検出するためのキーワード
EMAIL_DUPLICATE_ERROR_KEYWORD = 'このメールアドレスは既に登録されています'


@register.filter
def has_email_duplicate_error(form):
    """フォームにメールアドレス重複エラーがあるかをチェックする。

    使用例:
        {% if form|has_email_duplicate_error %}
    """
    if hasattr(form, 'errors') and 'email' in form.errors:
        for error in form.errors['email']:
            if EMAIL_DUPLICATE_ERROR_KEYWORD in str(error):
                return True
    return False


@register.filter
def get_other_errors(form):
    """メールアドレス重複エラー以外のエラーを取得する。

    使用例:
        {% for error in form|get_other_errors %}
    """
    other_errors = []
    if hasattr(form, 'errors'):
        for field, errors in form.errors.items():
            for error in errors:
                if EMAIL_DUPLICATE_ERROR_KEYWORD not in str(error):
                    other_errors.append(str(error))
    return other_errors
