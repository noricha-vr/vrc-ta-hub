"""Discord の告知チャンネルへ日時指定で送るメッセージの予約。"""
from __future__ import annotations

from django.conf import settings
from django.core.validators import MaxLengthValidator
from django.db import models
from django.utils import timezone

# Discord のメッセージ本文の上限（文字数）
DISCORD_CONTENT_MAX_LENGTH = 2000
# サーバーの全員に通知されるメンション。本文に含む時は保存時に確認チェックを必須にする
MASS_MENTION_TOKENS = ('@everyone', '@here')
# エラーの要約の最大長（画面と DB に残す分）
LAST_ERROR_MAX_LENGTH = 500


def contains_mass_mention(body: str) -> bool:
    """本文が @everyone / @here を含むかを返す。"""
    return any(token in body for token in MASS_MENTION_TOKENS)


def editable_conditions() -> dict:
    """編集・取り消し・送信の対象になる条件（予約中で、送信処理に取られていない）。

    QuerySet.editable() と、インスタンスの is_editable / is_sending の両方がここから作る。
    """
    return {'status': DiscordScheduledMessage.Status.SCHEDULED, 'lease_token': ''}


class DiscordScheduledMessageQuerySet(models.QuerySet):
    def editable(self):
        """予約中で、送信処理に取られていない予約に絞る（編集・取り消し・送信の対象）。"""
        return self.filter(**editable_conditions())

    def due(self, now):
        """送信日時と再試行の待ち時間を過ぎ、いま送ってよい予約に絞る。"""
        return self.editable().filter(scheduled_at__lte=now).filter(
            models.Q(next_attempt_at__isnull=True) | models.Q(next_attempt_at__lte=now),
        )


class DiscordScheduledMessage(models.Model):
    """告知チャンネルへ送るメッセージの予約。

    送信は Cloud Scheduler から 1 分ごとに呼ぶエンドポイントで行う。送信中の行には
    lease_token / lease_expires_at を付け、その間は編集・取り消し・別の実行による送信を受け付けない。
    """

    class Status(models.TextChoices):
        SCHEDULED = 'scheduled', '予約中'
        SENT = 'sent', '送信済み'
        FAILED = 'failed', '失敗'
        CANCELED = 'canceled', '取り消し'

    body = models.TextField(
        '本文',
        max_length=DISCORD_CONTENT_MAX_LENGTH,
        validators=[MaxLengthValidator(DISCORD_CONTENT_MAX_LENGTH)],
    )
    scheduled_at = models.DateTimeField('送信日時')
    mention_everyone_confirmed = models.BooleanField(
        '全員への通知を確認済み',
        default=False,
        help_text='本文の @everyone / @here で全員に通知することを、保存した人が確認したか',
    )
    status = models.CharField(
        '状態', max_length=16, choices=Status.choices, default=Status.SCHEDULED,
    )
    attempt_count = models.PositiveSmallIntegerField('送信を試みた回数', default=0)
    next_attempt_at = models.DateTimeField('次に送信を試みる日時', null=True, blank=True)
    lease_token = models.CharField('送信処理のリース', max_length=32, blank=True, default='')
    lease_expires_at = models.DateTimeField('リースの期限', null=True, blank=True)
    last_error = models.CharField(
        'エラーの要約', max_length=LAST_ERROR_MAX_LENGTH, blank=True, default='',
    )
    sent_at = models.DateTimeField('送信した日時', null=True, blank=True)
    discord_message_id = models.CharField(
        'Discord のメッセージ ID', max_length=32, blank=True, default='',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        verbose_name='作成者',
    )
    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    updated_at = models.DateTimeField('更新日時', auto_now=True)

    objects = DiscordScheduledMessageQuerySet.as_manager()

    class Meta:
        db_table = 'discord_scheduled_message'
        ordering = ['-scheduled_at', '-pk']
        verbose_name = 'Discord 告知の予約送信'
        verbose_name_plural = 'Discord 告知の予約送信'
        indexes = [
            models.Index(fields=['status', 'scheduled_at'], name='discord_msg_status_sched_idx'),
        ]

    def __str__(self) -> str:
        if self.scheduled_at is None:
            return f'#{self.pk} {self.get_status_display()}'
        scheduled_at = timezone.localtime(self.scheduled_at)
        return f'#{self.pk} {self.get_status_display()} {scheduled_at:%Y-%m-%d %H:%M}'

    @property
    def is_editable(self) -> bool:
        """編集・取り消しできるか（QuerySet.editable() と同じ条件）。"""
        return all(getattr(self, name) == value for name, value in editable_conditions().items())

    @property
    def is_sending(self) -> bool:
        """送信処理がこの予約を取って送っている最中か（予約中のうち、編集できる条件から外れているもの）。"""
        return self.status == self.Status.SCHEDULED and not self.is_editable

    @property
    def can_resend(self) -> bool:
        return self.status == self.Status.FAILED

    @property
    def is_waiting_retry(self) -> bool:
        """一度送信に失敗し、再試行を待っているか。"""
        return self.is_editable and self.attempt_count > 0

    @property
    def status_label(self) -> str:
        if self.is_sending:
            return '送信処理中'
        return self.get_status_display()

    @property
    def status_badge_class(self) -> str:
        if self.is_sending:
            return 'text-bg-info'
        return _STATUS_BADGE_CLASSES[self.status]


_STATUS_BADGE_CLASSES = {
    DiscordScheduledMessage.Status.SCHEDULED: 'text-bg-primary',
    DiscordScheduledMessage.Status.SENT: 'text-bg-success',
    DiscordScheduledMessage.Status.FAILED: 'text-bg-danger',
    DiscordScheduledMessage.Status.CANCELED: 'text-bg-secondary',
}
