"""Google カレンダー連携用フォーム。"""

from datetime import datetime, timedelta

from django import forms
from django.core.exceptions import ValidationError

from event.recurrence.calculator import get_nth_weekday_of_month

from .recurrence import (
    CALENDAR_WEEKDAY_CHOICES,
    RECURRENCE_CHOICES,
    WEEK_NUMBER_CHOICES,
)


class GoogleCalendarEventForm(forms.Form):

    community_name = forms.CharField(
        label='コミュニティ',
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'readonly': True}),
    )

    start_date = forms.DateField(
        label='開始日',
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control'
        }),
        help_text='初回開催日を選択してください。定期開催の場合は、選択した曜日・日付・週と一致させてください。'
    )

    start_time = forms.TimeField(
        label='開始時刻',
        widget=forms.TimeInput(attrs={
            'type': 'time',
            'class': 'form-control'
        }),
        initial='22:00',
        help_text='イベントの開始時刻を選択してください'
    )

    duration = forms.IntegerField(
        label='開催時間（分）',
        initial=60,
        min_value=15,
        max_value=480,
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
        help_text='イベントの開催時間を分単位で入力してください'
    )

    recurrence_type = forms.ChoiceField(
        choices=RECURRENCE_CHOICES,
        label='開催周期',
        initial='none',
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}),
        help_text='イベントの開催周期を選択してください'
    )

    weekday = forms.ChoiceField(
        choices=CALENDAR_WEEKDAY_CHOICES,
        label='曜日',
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'}),
        help_text='週次・隔週・毎月同じ曜日の場合、開催する曜日を選択してください'
    )

    monthly_day = forms.IntegerField(
        label='開催日',
        required=False,
        min_value=1,
        max_value=31,
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
        help_text='毎月同じ日に開催する場合、その日付を選択してください（1-31）'
    )

    week_number = forms.ChoiceField(
        choices=WEEK_NUMBER_CHOICES,
        label='週',
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'}),
        help_text='毎月同じ曜日の場合、第何週かを選択してください'
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # コミュニティが選択されている場合、そのコミュニティの設定を初期値として使用
        if 'initial' in kwargs and 'community' in kwargs['initial']:
            community = kwargs['initial']['community']
            if community:
                self.fields['start_time'].initial = community.start_time
                self.fields['duration'].initial = community.duration

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        recurrence_type = cleaned_data.get('recurrence_type')

        if start_date:
            # 過去の日付バリデーションを解除
            pass

            # 6ヶ月以上先の日付をチェック
            max_date = datetime.now().date() + timedelta(days=180)
            if start_date > max_date:
                raise ValidationError('6ヶ月以上先の日付は選択できません')

        # 定期開催の場合の追加バリデーション
        if recurrence_type in ['weekly', 'biweekly']:
            if not cleaned_data.get('weekday'):
                raise ValidationError('週次・隔週の場合は曜日を選択してください')

        elif recurrence_type == 'monthly_by_day':
            if not cleaned_data.get('weekday'):
                raise ValidationError('毎月同じ曜日の場合は曜日を選択してください')
            if not cleaned_data.get('week_number'):
                raise ValidationError('毎月同じ曜日の場合は第何週かを選択してください')

        elif recurrence_type == 'monthly_by_date':
            if not cleaned_data.get('monthly_day'):
                raise ValidationError('月次（日付指定）の場合は開催日を選択してください')

            # 31日がない月もあるため、28日までを推奨
            if cleaned_data.get('monthly_day') > 28:
                self.add_error(
                    'monthly_day', '月末の日付は月によって異なるため、28日以前の選択を推奨します')

        # 開始日は必ず初回開催日。選択した周期と違う日の登録を防ぐ。
        weekday = cleaned_data.get('weekday')
        if start_date and recurrence_type in ('weekly', 'biweekly', 'monthly_by_day') and weekday:
            weekday_index = [code for code, _ in CALENDAR_WEEKDAY_CHOICES].index(weekday)
            if start_date.weekday() != weekday_index:
                self.add_error('weekday', '開始日の曜日と同じ曜日を選択してください。')
            elif recurrence_type == 'monthly_by_day' and cleaned_data.get('week_number'):
                expected = get_nth_weekday_of_month(
                    start_date, weekday_index, int(cleaned_data['week_number']))
                if start_date != expected:
                    self.add_error('week_number', '開始日と一致する週を選択してください。')
        if (start_date and recurrence_type == 'monthly_by_date'
                and cleaned_data.get('monthly_day')
                and start_date.day != cleaned_data['monthly_day']):
            self.add_error('monthly_day', '開始日と同じ日付を選択してください。')

        return cleaned_data
