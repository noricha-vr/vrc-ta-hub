"""定期イベント関連フォームと共有定数。

RECURRENCE_CHOICES / CALENDAR_WEEKDAY_CHOICES / WEEK_NUMBER_CHOICES は
GoogleCalendarEventForm (calendar.py) からも参照されるためここに定義する。
"""

from django import forms
from django.core.exceptions import ValidationError

from ..models import RecurrenceRule


RECURRENCE_CHOICES = [
    ('none', '単発イベント'),
    ('weekly', '毎週'),
    ('biweekly', '隔週'),
    ('monthly_by_day', '毎月同じ曜日（例：第2月曜日）'),
    ('monthly_by_date', '毎月同じ日（例：毎月15日）'),
]

CALENDAR_WEEKDAY_CHOICES = [
    ('MO', '月曜日'),
    ('TU', '火曜日'),
    ('WE', '水曜日'),
    ('TH', '木曜日'),
    ('FR', '金曜日'),
    ('SA', '土曜日'),
    ('SU', '日曜日'),
]

WEEK_NUMBER_CHOICES = [
    (1, '第1'),
    (2, '第2'),
    (3, '第3'),
    (4, '第4'),
    (-1, '最終'),
]


class RecurringEventForm(forms.Form):
    """定期イベント作成フォーム"""
    # 基本情報
    base_date = forms.DateField(
        label='開始日',
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control',
            'id': 'id_base_date'
        }),
        help_text='定期イベントの最初の開催日'
    )

    start_time = forms.TimeField(
        label='開始時刻',
        widget=forms.TimeInput(attrs={
            'type': 'time',
            'class': 'form-control',
            'id': 'id_start_time'
        }),
        initial='22:00'
    )

    duration = forms.IntegerField(
        label='開催時間（分）',
        initial=60,
        min_value=15,
        max_value=480,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'id': 'id_duration'
        })
    )

    # 定期ルール
    frequency = forms.ChoiceField(
        label='開催パターン',
        choices=RecurrenceRule.FREQUENCY_CHOICES,
        initial='WEEKLY',
        widget=forms.Select(attrs={
            'class': 'form-control',
            'id': 'id_frequency',
            'onchange': 'updateFrequencyFields()'
        })
    )

    interval = forms.IntegerField(
        label='間隔',
        initial=1,
        min_value=1,
        max_value=12,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'id': 'id_interval'
        }),
        help_text='何週間/月ごとに開催するか'
    )

    week_of_month = forms.ChoiceField(
        label='第N週',
        required=False,
        choices=[
            (1, '第1'),
            (2, '第2'),
            (3, '第3'),
            (4, '第4'),
            (-1, '最終'),
        ],
        widget=forms.Select(attrs={
            'class': 'form-control',
            'id': 'id_week_of_month'
        })
    )

    custom_rule = forms.CharField(
        label='カスタムルール',
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'id': 'id_custom_rule',
            'rows': 3,
            'placeholder': '例: 第2火曜日と第4金曜日\n例: 月末の平日\n例: 祝日を除く毎週月曜日',
            'oninput': 'updatePreview()'
        }),
        help_text='複雑な開催パターンを日本語で記述してください'
    )

    end_date = forms.DateField(
        label='終了日',
        required=False,
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control',
            'id': 'id_end_date'
        }),
        help_text='定期イベントの終了日（省略可）'
    )

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        self.community = kwargs.pop('community', None)
        super().__init__(*args, **kwargs)

        if self.community:
            self.fields['start_time'].initial = self.community.start_time
            self.fields['duration'].initial = self.community.duration

    def clean(self):
        cleaned_data = super().clean()
        frequency = cleaned_data.get('frequency')

        if frequency == 'MONTHLY_BY_WEEK' and not cleaned_data.get('week_of_month'):
            raise ValidationError('第N曜日を選択してください')

        if frequency == 'OTHER' and not cleaned_data.get('custom_rule'):
            raise ValidationError('カスタムルールを入力してください')

        base_date = cleaned_data.get('base_date')
        end_date = cleaned_data.get('end_date')

        if base_date and end_date and end_date <= base_date:
            raise ValidationError('終了日は開始日より後の日付を指定してください')

        return cleaned_data
