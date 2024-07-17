from django import forms

from event_calendar.models import CalendarEntry
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

    # CalendarEntryのフィールドを追加
    event_detail = forms.CharField(label='イベント内容', widget=forms.Textarea(attrs={'class': 'form-control'}),
                                   required=False)
    event_genres = forms.MultipleChoiceField(
        label='イベントジャンル',
        choices=CalendarEntry.EVENT_GENRE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False
    )
    join_condition = forms.CharField(label='参加条件（モデル、人数制限など）',
                                     widget=forms.Textarea(attrs={'class': 'form-control'}),
                                     required=False)

    how_to_join = forms.CharField(label='参加方法', widget=forms.Textarea(attrs={'class': 'form-control'}),
                                  required=False)
    note = forms.CharField(label='備考', widget=forms.Textarea(attrs={'class': 'form-control'}), required=False)

    is_overseas_user = forms.BooleanField(label='海外ユーザー向け告知', required=False)

    class Meta:
        model = Community
        fields = [
            'name', 'start_time', 'duration', 'weekdays', 'frequency', 'organizers',
            'group_url', 'organizer_url', 'sns_url', 'discord', 'twitter_hashtag',
            'poster_image', 'description', 'platform', 'tags'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'start_time': forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'duration': forms.NumberInput(attrs={'class': 'form-control'}),
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
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['weekdays'].initial = self.instance.weekdays
            self.fields['tags'].initial = self.instance.tags
            try:
                calendar_entry = self.instance.calendar_entry
                self.fields['join_condition'].initial = calendar_entry.join_condition
                self.fields['event_detail'].initial = calendar_entry.event_detail
                self.fields['how_to_join'].initial = calendar_entry.how_to_join
                self.fields['note'].initial = calendar_entry.note
                self.fields['is_overseas_user'].initial = calendar_entry.is_overseas_user
                self.fields['event_genres'].initial = calendar_entry.event_genres
            except CalendarEntry.DoesNotExist:
                pass
