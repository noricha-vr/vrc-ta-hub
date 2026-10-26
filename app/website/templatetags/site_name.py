"""リクエストの有無によらず共通のサイト名を出力するタグ。"""

from django import template

from website.constants import SITE_NAME, SITE_NAME_SEO

register = template.Library()


@register.simple_tag
def site_name():
    return SITE_NAME


@register.simple_tag
def site_name_seo():
    return SITE_NAME_SEO
