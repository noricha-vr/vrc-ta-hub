"""曜日など、集会まわりの共通定数.

models を import せずに参照できるよう軽量モジュールとして切り出している
（twitter / api_v1 / templatetags から循環 import なしで使うため）。
曜日表記の正本はここ 1 箇所。
"""

from datetime import date

from django.db import models


# 発表の持ち時間・開始のずれの上限（集会設定と申請フォームで共有）
MAX_LT_DURATION_MINUTES = 240
MAX_LT_START_OFFSET_MINUTES = 1440

_WEEKDAY_CODES = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')

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


def weekday_code(value: date) -> str:
    """開催日からロケール非依存の曜日コードを返す。

    Args:
        value: 曜日コードへ変換する日付

    Returns:
        ``Mon`` から ``Sun`` の固定曜日コード
    """
    return _WEEKDAY_CODES[value.weekday()]


# 発表申請の「追加情報テンプレート」を主催者が未設定の時に設定画面へ出す既定文。
# 撮影の可否は発表ごとの選択肢（EventDetail.recording_policy）で聞くため、ここには含めない。
DEFAULT_LT_APPLICATION_TEMPLATE = "【発表概要】\n\n【スライド公開】OK / NG"


class RecordingPolicy(models.TextChoices):
    """発表ごとの撮影の扱い。

    EventDetail.recording_policy と、集会の撮影ステータスの初期値（Community.default_recording_policy）で共有する。
    community は event を import できないため、正本はここに置く。
    """

    FORBIDDEN = 'forbidden', '禁止（撮影しない）'
    ALLOWED = 'allowed', '許可（撮影するが公開しない）'
    PUBLIC = 'public', '公開（撮影して YouTube で公開）'


# フォームの表示順・選択肢・表示文言を定義し、モデルの choices（migration）には影響させない。
# allowed は過去データ互換のためにモデル定義のみ残し、新規選択には出さない。
RECORDING_POLICY_FORM_CHOICES = [
    (RecordingPolicy.PUBLIC.value, '許可する（ボットで撮影して YouTube で公開）'),
    (RecordingPolicy.FORBIDDEN.value, '許可しない（ボットで撮影しない）'),
]
RECORDING_POLICY_FORM_VALUES = frozenset(value for value, _ in RECORDING_POLICY_FORM_CHOICES)
RECORDING_POLICY_DISPLAY_LABELS = {
    RecordingPolicy.PUBLIC.value: '許可する（ボットで撮影して YouTube で公開）',
    RecordingPolicy.FORBIDDEN.value: '許可しない（ボットで撮影しない）',
    RecordingPolicy.ALLOWED.value: '旧設定（撮影するが公開しない）',
}

RECORDING_POLICY_LABEL = 'ボットによる自動撮影'
RECORDING_POLICY_ADMIN_HELP_TEXT = (
    'ハブの撮影ボットによる録画・YouTube 公開の可否です'
    '（集会独自の撮影や参加者の写真撮影を制限する設定ではありません）。'
)
RECORDING_ALLOWED_LABEL = 'ボットによる自動撮影の許可'
DEFAULT_RECORDING_POLICY_LABEL = 'ボット撮影許可の初期値'
DEFAULT_RECORDING_POLICY_ADMIN_HELP_TEXT = (
    '発表申請フォームで、ボットによる自動撮影の初期値になります。登壇者は申請時に変えられます。'
)
LEGACY_RECORDING_POLICY_NOTICE = (
    f'現在は「{RECORDING_POLICY_DISPLAY_LABELS[RecordingPolicy.ALLOWED.value]}」が設定されています。'
    '選択せずに保存すると現在の設定が維持されます。'
)


def recording_policy_admin_choices(current_value: str | None) -> list[tuple[str, str]]:
    """Django 管理画面向け選択肢。新規・現行値では 2 択のみ、既存 allowed 編集時のみ現在値を保持できるよう含める。"""
    if current_value == RecordingPolicy.ALLOWED:
        return [
            *RECORDING_POLICY_FORM_CHOICES,
            (RecordingPolicy.ALLOWED.value, RECORDING_POLICY_DISPLAY_LABELS[RecordingPolicy.ALLOWED.value]),
        ]
    return list(RECORDING_POLICY_FORM_CHOICES)
