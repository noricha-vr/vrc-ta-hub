# twitter/forms.py

from django import forms

from .models import TwitterTemplate


class TwitterTemplateForm(forms.ModelForm):
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
                'placeholder': '今夜は{event_name}！\n\n【タイムスケジュール】\n{date}\n{time}～ 開場\n\n{time}～ {speaker}さん\n『{theme}』\n\nみんな遊びに来てね～😊\nJoin先・VRCグループ : https://**\n\n#{event_name} #VRChat'
            }),
        }
        help_texts = {
            'template': '利用可能な変数: {event_name}, {date}, {time}, {speaker}, {theme}'
        }
