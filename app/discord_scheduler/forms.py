"""Discord予約投稿の入力。日時は常に日本時間として扱う。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from django import forms
from django.utils import timezone

from .models import ScheduledDiscordPost


JST = ZoneInfo('Asia/Tokyo')


class JSTDateTimeField(forms.DateTimeField):
    def to_python(self, value):
        # datetime-local にはタイムゾーンが付かない。ユーザーやサーバーの
        # current_timezone に関係なく、日本時間の入力として解釈する。
        with timezone.override(JST):
            return super().to_python(value)

    def prepare_value(self, value):
        if isinstance(value, datetime) and timezone.is_aware(value):
            return timezone.localtime(value, JST).replace(tzinfo=None)
        return super().prepare_value(value)


class ScheduledDiscordPostForm(forms.ModelForm):
    scheduled_at = JSTDateTimeField(
        label='投稿日時（日本時間・JST）',
        input_formats=['%Y-%m-%dT%H:%M'],
        widget=forms.DateTimeInput(
            format='%Y-%m-%dT%H:%M',
            attrs={'type': 'datetime-local', 'class': 'form-control'},
        ),
        error_messages={
            'required': '投稿日時を入力してください。',
            'invalid': '正しい投稿日時を入力してください。',
        },
    )

    class Meta:
        model = ScheduledDiscordPost
        fields = ['content', 'scheduled_at']
        labels = {'content': '投稿本文'}
        widgets = {
            'content': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 10,
                'maxlength': 2000,
                'placeholder': 'Discordに投稿する文章を入力してください。URLも貼り付けられます。',
            }),
        }
        error_messages = {'content': {'required': '投稿本文を入力してください。'}}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Markdownの字下げや改行を、予約時に勝手に取り除かない。
        self.fields['content'].strip = False

    def clean_scheduled_at(self):
        scheduled_at = self.cleaned_data['scheduled_at']
        if scheduled_at <= timezone.now():
            raise forms.ValidationError('投稿日時は現在より後の日時を指定してください。')
        return scheduled_at
