from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)


WEEKDAY_JP = {
    'Sun': '日曜日', 'Mon': '月曜日', 'Tue': '火曜日', 'Wed': '水曜日',
    'Thu': '木曜日', 'Fri': '金曜日', 'Sat': '土曜日', 'Other': 'その他',
}

WEEKDAY_ABBR = {
    'Sun': '日', 'Mon': '月', 'Tue': '火', 'Wed': '水',
    'Thu': '木', 'Fri': '金', 'Sat': '土', 'Other': '他',
}


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
