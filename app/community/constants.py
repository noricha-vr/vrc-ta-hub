"""曜日など、集会まわりの共通定数.

models を import せずに参照できるよう軽量モジュールとして切り出している
（twitter / api_v1 / templatetags から循環 import なしで使うため）。
曜日表記の正本はここ 1 箇所。
"""

WEEKDAY_CHOICES = (
    ('Sun', '日曜日'),
    ('Mon', '月曜日'),
    ('Tue', '火曜日'),
    ('Wed', '水曜日'),
    ('Thu', '木曜日'),
    ('Fri', '金曜日'),
    ('Sat', '土曜日'),
    ('Other', 'その他')
)

# コード → 日本語フル名（'Sun' → '日曜日'）
WEEKDAY_JP = dict(WEEKDAY_CHOICES)

# コード → 1文字略称。'Other' だけはフル名の先頭文字（'そ'）ではなく '他' を使うため
# 機械導出できず、ここで明示する。
WEEKDAY_ABBR = {code: label[0] for code, label in WEEKDAY_CHOICES if code != 'Other'}
WEEKDAY_ABBR['Other'] = '他'

# 表示順（WEEKDAY_CHOICES の定義順がそのまま並び順）
WEEKDAY_ORDER = {code: index for index, (code, _label) in enumerate(WEEKDAY_CHOICES)}
