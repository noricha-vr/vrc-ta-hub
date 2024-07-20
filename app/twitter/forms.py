# twitter/forms.py

from django import forms

from .models import TwitterTemplate


class TwitterTemplateForm(forms.ModelForm):
    template_initial = (
        '今夜は{event_name}！\n\n'
        '【タイムスケジュール】\n'
        '{date}\n'
        '{time}～ 開場\n\n'
        '{time}～ {speaker}さん\n'
        '『{theme}』\n\n'
        'みんな遊びに来てね～😊\n'
        'Join先・VRCグループ : https://**\n\n'
        '#VRChat'
    )

    class Meta:
        model = TwitterTemplate
        fields = ['name', 'template']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'テンプレート名（例：標準告知）'
            }),
            'template': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 18,
            }),
        }
        help_texts = {
            'template': '利用可能な変数: {event_name}, {date}, {time}, {speaker}, {theme}'
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['template'].initial = self.template_initial
