from django import forms
from .models import Community


class CommunitySearchForm(forms.Form):
    query = forms.CharField(
        label='検索',
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'キーワードを入力',
            'class': 'form-control',
            'style': 'width: 80%;'
        })
    )
    weekdays = forms.MultipleChoiceField(
        label='曜日',
        choices=Community.WEEKDAY_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple
    )
