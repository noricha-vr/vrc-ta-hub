from django import forms
from .models import WEEKDAY_CHOICES


class CommunitySearchForm(forms.Form):
    query = forms.CharField(
        label='検索',
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'キーワードを入力',
            'class': 'form-control',
        })
    )
    weekdays = forms.MultipleChoiceField(
        label='曜日',
        choices=WEEKDAY_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple
    )
