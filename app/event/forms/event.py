"""イベント検索・作成フォーム。"""

from django import forms
from django.utils import timezone

from community.models import WEEKDAY_CHOICES, TAGS
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
            timezone.localdate().isoformat()
        )

    def clean_date(self):
        """当日以降かつ同時刻の重複がない開催日を返す。"""
        new_date = self.cleaned_data['date']
        if new_date < timezone.localdate():
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
