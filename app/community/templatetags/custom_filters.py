from django import template

from community.constants import WEEKDAY_ABBR, WEEKDAY_JP  # noqa: F401  既存の参照名を維持する

register = template.Library()


@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)


@register.filter
def weekday_abbr(weekday):
    normalized = weekday.title() if isinstance(weekday, str) else weekday
    return WEEKDAY_ABBR.get(normalized, weekday)


@register.filter
def weekday_jp(weekday):
    """英語曜日略称 → 日本語フル名（例: 'Sun'/'SUN' → '日曜日'）"""
    normalized = weekday.title() if isinstance(weekday, str) else weekday
    return WEEKDAY_JP.get(normalized, weekday)


@register.filter
def date_weekday_jp(date_obj):
    """dateオブジェクト → 日本語曜日フル名"""
    if date_obj:
        weekday_map = ['月曜日', '火曜日', '水曜日', '木曜日', '金曜日', '土曜日', '日曜日']
        return weekday_map[date_obj.weekday()]
    return ''
