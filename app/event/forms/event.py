"""イベント検索・作成フォーム。"""

from django import forms

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
