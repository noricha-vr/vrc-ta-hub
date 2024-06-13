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


from django import forms
from django.forms.widgets import CheckboxSelectMultiple
from .models import Community, WEEKDAY_CHOICES, TAGS


class CommunityForm(forms.ModelForm):
    weekdays = forms.MultipleChoiceField(
        choices=WEEKDAY_CHOICES,
        widget=CheckboxSelectMultiple(),
        required=False,
    )
    tags = forms.MultipleChoiceField(  # tags フィールドを追加
        choices=TAGS,
        widget=CheckboxSelectMultiple(),
        required=False,
    )

    class Meta:
        model = Community
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.initial['weekdays'] = self.instance.weekdays
            self.initial['tags'] = self.instance.tags  # tags の初期値を設定

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.weekdays = self.cleaned_data['weekdays']
        instance.tags = self.cleaned_data['tags']  # tags を保存
        if commit:
            instance.save()
        return instance
