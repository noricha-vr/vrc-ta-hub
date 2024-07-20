# twitter/forms.py

from django import forms

from .models import TwitterTemplate


class TwitterTemplateForm(forms.ModelForm):
    class Meta:
        model = TwitterTemplate
        fields = ['name', 'template']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'template': forms.Textarea(attrs={'class': 'form-control', 'rows': 5}),
        }
        help_texts = {
            'template': '利用可能な変数: {event_name}, {date}, {time}, {speaker}, {theme}'
        }
