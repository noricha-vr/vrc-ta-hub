"""解析カード共通の時間表示。"""
from django import template

register = template.Library()


@register.filter
def watch_duration(seconds):
    """秒を M:SS、1時間以上を H:MM:SS で表示する（秒未満は切り捨て）。"""
    if seconds is None:
        return ''
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes}:{seconds:02d}'
