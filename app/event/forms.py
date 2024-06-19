from django import forms
from django.core.exceptions import ValidationError

from community.models import WEEKDAY_CHOICES, TAGS
from .models import EventDetail


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


class EventDetailForm(forms.ModelForm):
    start_time = forms.TimeField(
        label='開始時間',
        widget=forms.TimeInput(
            attrs={'type': 'time', 'class': 'form-control w-25'})  # Bootstrapのクラスを追加
    )
    duration = forms.IntegerField(
        label='発表時間（分）',
        min_value=1,
        widget=forms.NumberInput(attrs={'class': 'form-control w-25'})
    )

    class Meta:
        model = EventDetail
        fields = ['theme', 'speaker', 'start_time', 'duration', 'slide_url', 'slide_file', 'youtube_url', 'contents']
        widgets = {
            'youtube_url': forms.URLInput(attrs={'class': 'form-control'}),
            'slide_url': forms.URLInput(attrs={'class': 'form-control'}),
            'slide_file': forms.ClearableFileInput(attrs={'class': 'form-control-file'}),
            'speaker': forms.TextInput(attrs={'class': 'form-control'}),
            'start_time': forms.TextInput(attrs={'class': 'form-control'}),
            'duration': forms.TextInput(attrs={'class': 'form-control'}),
            'theme': forms.TextInput(attrs={'class': 'form-control'}),
            'contents': forms.Textarea(attrs={'class': 'form-control', 'rows': '18'}),
        }
        help_texts = {
            'contents': '※ Markdown形式で記述してください。',
            'duration': '単位は分'  # help_text を追加
        }

    def clean_slide_file(self):
        slide_file = self.cleaned_data.get('slide_file')
        if slide_file:
            if slide_file.size > 30 * 1024 * 1024:  # 30MB
                raise ValidationError('ファイルサイズが30MBを超えています。')
        return slide_file
