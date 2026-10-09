from __future__ import annotations

from zoneinfo import ZoneInfo

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import DISCORD_CONTENT_MAX_LENGTH, DiscordScheduledMessage, contains_mass_mention

JST = ZoneInfo('Asia/Tokyo')
DATETIME_LOCAL_FORMAT = '%Y-%m-%dT%H:%M'
# datetime-local の刻み（秒）。分単位で選ばせる
DATETIME_LOCAL_STEP_SECONDS = 60
BODY_TEXTAREA_ROWS = 10

PAST_DATETIME_ERROR = '過去の日時は指定できません。今より後の日時を選んでください。'
MASS_MENTION_CONFIRM_ERROR = (
    '本文に @everyone または @here があります。サーバーの全員に通知されることを確認して、チェックを入れてください。'
)


class DiscordContentField(forms.CharField):
    """改行を LF にそろえてから文字数を数える本文の欄。

    ブラウザはテキストエリアの改行を CRLF で送るため、そのまま数えると Discord より多く数えてしまう。
    """

    def to_python(self, value):
        value = super().to_python(value)
        return value.replace('\r\n', '\n').replace('\r', '\n')


class JSTDateTimeLocalField(forms.DateTimeField):
    """datetime-local の入力を日本時間として読み、初期値も日本時間で表示する欄。"""

    def __init__(self, **kwargs):
        kwargs.setdefault('input_formats', [DATETIME_LOCAL_FORMAT])
        kwargs.setdefault('widget', forms.DateTimeInput(
            attrs={'type': 'datetime-local', 'step': DATETIME_LOCAL_STEP_SECONDS, 'class': 'form-control'},
            format=DATETIME_LOCAL_FORMAT,
        ))
        super().__init__(**kwargs)

    def prepare_value(self, value):
        with timezone.override(JST):
            return super().prepare_value(value)

    def to_python(self, value):
        with timezone.override(JST):
            return super().to_python(value)


class DiscordScheduledMessageForm(forms.ModelForm):
    """予約の作成・編集フォーム。

    @everyone / @here の確認チェックは毎回未チェックで出し、保存のたびに確認してもらう
    （本文を後から編集して全員宛てのメンションを足した時に、前の確認を引き継がないため）。
    """

    body = DiscordContentField(
        label='本文',
        max_length=DISCORD_CONTENT_MAX_LENGTH,
        widget=forms.Textarea(
            attrs={'class': 'form-control', 'rows': BODY_TEXTAREA_ROWS, 'data-role': 'discord-body'},
        ),
    )
    scheduled_at = JSTDateTimeLocalField(label='送信日時（日本時間）')
    confirm_mass_mention = forms.BooleanField(
        label='@everyone / @here で、サーバーの全員に通知されることを確認しました',
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    class Meta:
        model = DiscordScheduledMessage
        fields = ['body', 'scheduled_at']

    def __init__(self, *args, now=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._now = now
        # 過去の日時をブラウザ側でも選びにくくする（判定の正はサーバー側の clean_scheduled_at）
        current = timezone.localtime(now or timezone.now(), JST)
        self.fields['scheduled_at'].widget.attrs['min'] = current.strftime(DATETIME_LOCAL_FORMAT)

    def full_clean(self):
        super().full_clean()
        for name in self.errors:
            if name in self.fields:
                widget = self.fields[name].widget
                widget.attrs['class'] = f"{widget.attrs.get('class', '')} is-invalid".strip()

    @property
    def shows_mass_mention_confirm(self) -> bool:
        """確認チェックの欄を最初から出すか（本文に @everyone / @here がある時）。"""
        return contains_mass_mention(str(self['body'].value() or ''))

    def clean_scheduled_at(self):
        scheduled_at = self.cleaned_data['scheduled_at'].replace(second=0, microsecond=0)
        if scheduled_at <= (self._now or timezone.now()):
            raise ValidationError(PAST_DATETIME_ERROR)
        return scheduled_at

    def clean(self):
        cleaned_data = super().clean()
        body = cleaned_data.get('body') or ''
        if contains_mass_mention(body) and not cleaned_data.get('confirm_mass_mention'):
            self.add_error('confirm_mass_mention', MASS_MENTION_CONFIRM_ERROR)
        return cleaned_data

    @property
    def mention_everyone_confirmed(self) -> bool:
        """保存する「全員への通知を確認済み」の値（本文に全員宛てのメンションがあり、確認した時だけ True）。"""
        body = self.cleaned_data.get('body') or ''
        return contains_mass_mention(body) and bool(self.cleaned_data.get('confirm_mass_mention'))

    def save(self, commit=True):
        self.instance.mention_everyone_confirmed = self.mention_everyone_confirmed
        return super().save(commit=commit)
