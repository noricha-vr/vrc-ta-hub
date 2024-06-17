from django import forms
from .models import WEEKDAY_CHOICES, TAGS


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
    tags = forms.MultipleChoiceField(
        label='タグ',
        choices=TAGS,
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


from django import forms
from .models import Community


class CommunityUpdateForm(forms.ModelForm):
    weekdays = forms.MultipleChoiceField(
        label='曜日',
        choices=WEEKDAY_CHOICES,
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-inline'}),
        required=False
    )

    tags = forms.MultipleChoiceField(
        label='タグ',
        choices=TAGS,
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-inline'}),
        required=False
    )

    class Meta:
        model = Community
        fields = ['name', 'start_time', 'duration', 'weekdays', 'frequency', 'organizers', 'group_url', 'organizer_url',
                  'sns_url', 'discord', 'twitter_hashtag', 'poster_image', 'description', 'platform', 'tags']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'start_time': forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'duration': forms.NumberInput(attrs={'class': 'form-control'}),
            'weekdays': forms.SelectMultiple(attrs={'class': 'form-control'}),
            'frequency': forms.TextInput(attrs={'class': 'form-control'}),
            'organizers': forms.TextInput(attrs={'class': 'form-control'}),
            'group_url': forms.URLInput(attrs={'class': 'form-control'}),
            'organizer_url': forms.URLInput(attrs={'class': 'form-control'}),
            'sns_url': forms.URLInput(attrs={'class': 'form-control'}),
            'discord': forms.URLInput(attrs={'class': 'form-control'}),
            'twitter_hashtag': forms.TextInput(attrs={'class': 'form-control'}),
            'poster_image': forms.FileInput(attrs={'class': 'form-control-file'}),
            'description': forms.Textarea(attrs={'class': 'form-control'}),
            'platform': forms.Select(attrs={'class': 'form-control'}),
            'tags': forms.SelectMultiple(attrs={'class': 'form-control'}),
        }
        labels = {
            'sns_url': 'SNS URL',
        }
        help_texts = {
            'name': '※ Googleカレンダーと同期するためにカレンダーの予定名と集会名が完全に一致する必要があります',
        }
