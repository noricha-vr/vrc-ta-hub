from django import template
from django.utils.safestring import mark_safe
from event.libs import convert_markdown

register = template.Library()


@register.filter(name='markdown')
def markdown_filter(value):
    """MarkdownをHTMLに変換するフィルタ"""
    if not value:
        return ''
    html = convert_markdown(value)
    return mark_safe(html)