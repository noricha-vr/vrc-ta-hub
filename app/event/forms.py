from community.models import WEEKDAY_CHOICES, TAGS
from django import forms
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


from django.core.exceptions import ValidationError


class EventDetailForm(forms.ModelForm):
    class Meta:
        model = EventDetail
        fields = ['theme', 'speaker', 'slide_url', 'slide_file', 'youtube_url', ]
        widgets = {
            'youtube_url': forms.URLInput(attrs={'class': 'form-control'}),
            'slide_url': forms.URLInput(attrs={'class': 'form-control'}),
            'slide_file': forms.ClearableFileInput(attrs={'class': 'form-control-file'}),
            'speaker': forms.TextInput(attrs={'class': 'form-control'}),
            'theme': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def clean_slide_file(self):
        slide_file = self.cleaned_data.get('slide_file')
        if slide_file:
            if slide_file.size > 30 * 1024 * 1024:  # 30MB
                raise ValidationError('ファイルサイズが30MBを超えています。')
        return slide_file
