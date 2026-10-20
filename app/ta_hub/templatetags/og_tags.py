"""OGP 用の静的画像 URL を絶対 URL にする。"""
from django import template

from ta_hub.og_image import absolute_static_url

register = template.Library()


@register.simple_tag(takes_context=True)
def og_static(context, path):
    return absolute_static_url(context['request'], path)
