"""告知本文の Discord プレビュー用フィルタ。"""
from django import template

from announcement.discord_markdown import render_discord_markdown

register = template.Library()


@register.filter
def discord_markdown(value):
    return render_discord_markdown(value)
