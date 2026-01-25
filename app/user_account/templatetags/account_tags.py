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
