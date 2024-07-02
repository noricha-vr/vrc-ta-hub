from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)


@register.filter
def weekday_abbr(weekday):
    weekday_dict = {
        'Sun': '日', 'Mon': '月', 'Tue': '火', 'Wed': '水',
        'Thu': '木', 'Fri': '金', 'Sat': '土', 'Other': '他'
    }
    return weekday_dict.get(weekday, weekday)
