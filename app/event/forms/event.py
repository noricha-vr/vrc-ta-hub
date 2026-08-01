"""イベント検索・作成フォーム。"""

from django import forms

from community.constants import WEEKDAY_CHOICES
from community.models import TAGS
from utils.vrchat_time import get_vrchat_today
from ..models import Event


class EventSearchForm(forms.Form):
    name = forms.CharField(
        label='集会名',
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '集会名を入力',
        })
    )
    weekday = forms.MultipleChoiceField(
        label='曜日',
        choices=WEEKDAY_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple()
    )
    tags = forms.MultipleChoiceField(
        label='タグ',
        choices=TAGS,
        required=False,
        widget=forms.CheckboxSelectMultiple()
    )


class EventUpdateForm(forms.ModelForm):
    """開始時刻のみを編集するフォーム。

    date / community は変更不可（UniqueConstraint (community, date, start_time) と
    Google カレンダー連携の副作用範囲を最小化するため、フィールドを start_time に限定）。
    """

    class Meta:
        model = Event
        fields = ['start_time']
        widgets = {
            'start_time': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
        }

    def clean_start_time(self):
        start_time = self.cleaned_data.get('start_time')
        if start_time is None or self.instance is None or self.instance.pk is None:
            return start_time
        # UniqueConstraint (community, date, start_time) 対応
        conflict = Event.objects.filter(
            community=self.instance.community,
            date=self.instance.date,
            start_time=start_time,
        ).exclude(pk=self.instance.pk).exists()
        if conflict:
            raise forms.ValidationError('同じ日時にすでにイベントが登録されています。')
        return start_time


class EventCreateForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = ['date', 'start_time', 'duration']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'start_time': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
            'duration': forms.NumberInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)  # requestオブジェクトを受け取る
        super().__init__(*args, **kwargs)
        if self.request and self.request.user.is_authenticated:
            membership = self.request.user.community_memberships.select_related('community').first()
            if membership:
                community = membership.community
                self.fields['start_time'].initial = community.start_time  # Communityから初期値を設定
                self.fields['duration'].initial = community.duration  # Communityから初期値を設定

    def clean(self):
        cleaned_data = super().clean()
        # 過去日付のバリデーションを解除（何もしない）
        return cleaned_data


class EventDateUpdateForm(forms.ModelForm):
    """イベントの開催日だけを変更するフォーム。"""

    class Meta:
        model = Event
        fields = ['date']
        widgets = {
            'date': forms.DateInput(attrs={
                'type': 'date',
                'class': 'form-control',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['date'].widget.attrs['min'] = (
            get_vrchat_today().isoformat()
        )

    def clean_date(self):
        """当日以降かつ同時刻の重複がない開催日を返す。"""
        new_date = self.cleaned_data['date']
        # 「今日」の基準は my_list の編集可否判定・EventUpdateView と揃える
        # （深夜0-4時の開催回を過去扱いして編集不能にしないため）
        if new_date < get_vrchat_today():
            raise forms.ValidationError('開催日は本日以降を指定してください。')

        duplicate = Event.objects.filter(
            community=self.instance.community,
            date=new_date,
            start_time=self.instance.start_time,
        ).exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise forms.ValidationError(
                '同じ集会・開催日・開始時刻のイベントが既に存在します。'
            )
        return new_date
