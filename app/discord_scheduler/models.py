from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


def validate_discord_content(value: str) -> None:
    """Discordの上限を、絵文字を含めて送信前に検証する。"""
    if not value.strip():
        raise ValidationError("投稿本文を入力してください。", code="blank")
    try:
        length = len(value.encode("utf-16-le")) // 2
    except UnicodeEncodeError:
        raise ValidationError("投稿本文に使用できない文字が含まれています。", code="invalid") from None
    if length > 2000:
        raise ValidationError(
            "投稿本文は2,000文字以内にしてください。一部の絵文字は2文字分として数えます。",
            code="max_length",
        )


class ScheduledDiscordPost(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "予約済み"
        SENDING = "sending", "送信中"
        SENT = "sent", "送信済み"
        FAILED = "failed", "失敗"
        NEEDS_REVIEW = "needs_review", "要確認"
        CANCELLED = "cancelled", "取消済み"

    content = models.TextField("投稿本文", max_length=2000, validators=[validate_discord_content])
    scheduled_at = models.DateTimeField("投稿予定日時")
    status = models.CharField("状態", max_length=20, choices=Status.choices, default=Status.SCHEDULED)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="作成者",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scheduled_discord_posts",
    )
    created_at = models.DateTimeField("作成日時", auto_now_add=True)
    updated_at = models.DateTimeField("更新日時", auto_now=True)
    started_at = models.DateTimeField("送信開始日時", null=True, blank=True)
    sent_at = models.DateTimeField("送信日時", null=True, blank=True)
    next_attempt_at = models.DateTimeField("送信制限の解除予定日時", null=True, blank=True)
    message_url = models.URLField("Discordの投稿URL", max_length=500, blank=True)
    error_message = models.TextField("処理結果の補足", blank=True)

    class Meta:
        ordering = ["scheduled_at", "pk"]
        verbose_name = "Discord予約投稿"
        verbose_name_plural = "Discord予約投稿"
        indexes = [models.Index(fields=["status", "scheduled_at"], name="discord_sched_due_idx")]

    def __str__(self):
        return f"{self.scheduled_at}: {self.content[:40]}"
