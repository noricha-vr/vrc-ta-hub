from django.db import models
from django.core.exceptions import ValidationError


class VketCollaboration(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', '下書き'
        OPEN = 'open', '受付中'
        CLOSED = 'closed', '受付終了'
        ARCHIVED = 'archived', 'アーカイブ'

    name = models.CharField('コラボ名', max_length=200)
    period_start = models.DateField('開催期間 開始')
    period_end = models.DateField('開催期間 終了')
    registration_deadline = models.DateField('参加表明締切（Step 1）')
    lt_deadline = models.DateField('LT情報締切（Step 2）')
    status = models.CharField('状態', max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    hashtags = models.JSONField('ハッシュタグ', default=list, blank=True)
    description = models.TextField('案内文', blank=True)
    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    updated_at = models.DateTimeField('更新日時', auto_now=True)

    class Meta:
        verbose_name = 'Vketコラボ'
        verbose_name_plural = 'Vketコラボ'
        db_table = 'vket_collaboration'
        ordering = ['-period_start', '-id']

    def __str__(self) -> str:
        return self.name

    def clean(self):
        errors = {}
        if self.period_start and self.period_end and self.period_start > self.period_end:
            errors['period_end'] = '開催期間の終了日は開始日以降にしてください。'
        if self.registration_deadline and self.lt_deadline and self.registration_deadline > self.lt_deadline:
            errors['lt_deadline'] = 'LT情報締切は参加表明締切以降にしてください。'
        if errors:
            raise ValidationError(errors)


class VketParticipation(models.Model):
    collaboration = models.ForeignKey(
        VketCollaboration,
        on_delete=models.CASCADE,
        related_name='participations',
        verbose_name='コラボ',
    )
    community = models.ForeignKey(
        'community.Community',
        on_delete=models.CASCADE,
        related_name='vket_participations',
        verbose_name='集会',
    )
    event = models.ForeignKey(
        'event.Event',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='vket_participations',
        verbose_name='参加イベント',
    )
    note = models.TextField('備考（主催者）', blank=True)
    admin_note = models.TextField('備考（運営）', blank=True)
    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    updated_at = models.DateTimeField('更新日時', auto_now=True)

    class Meta:
        verbose_name = 'Vket参加'
        verbose_name_plural = 'Vket参加'
        db_table = 'vket_participation'
        constraints = [
            models.UniqueConstraint(
                fields=['collaboration', 'community'],
                name='unique_vket_participation_per_community',
            )
        ]

    def __str__(self) -> str:
        return f'{self.collaboration.name} - {self.community.name}'
