import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class VketCollaboration(models.Model):
    class Phase(models.TextChoices):
        DRAFT = "draft", "下書き"
        ENTRY_OPEN = "entry_open", "参加受付中"
        SCHEDULING = "scheduling", "日程調整中"
        LT_COLLECTION = "lt_collection", "LT情報回収中"
        ANNOUNCEMENT = "announcement", "告知確認中"
        LOCKED = "locked", "確定"
        ARCHIVED = "archived", "アーカイブ"

    slug = models.SlugField("スラッグ", unique=True, max_length=100)
    name = models.CharField("コラボ名", max_length=200)
    phase = models.CharField(
        "フェーズ",
        max_length=20,
        choices=Phase.choices,
        default=Phase.DRAFT,
        db_index=True,
    )
    period_start = models.DateField("開催期間 開始")
    period_end = models.DateField("開催期間 終了")
    registration_deadline = models.DateField("参加表明締切（Step 1）")
    lt_deadline = models.DateField("LT情報締切（Step 2）")
    hashtags = models.JSONField("ハッシュタグ", default=list, blank=True)
    description = models.TextField("案内文", blank=True)
    settings_json = models.JSONField("設定（JSON）", null=True, blank=True)
    created_at = models.DateTimeField("作成日時", auto_now_add=True)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        verbose_name = "Vketコラボ"
        verbose_name_plural = "Vketコラボ"
        db_table = "vket_collaboration"
        ordering = ["-period_start", "-id"]

    def __str__(self) -> str:
        return self.name

    def clean(self):
        errors = {}
        if self.period_start and self.period_end and self.period_start > self.period_end:
            errors["period_end"] = "開催期間の終了日は開始日以降にしてください。"
        if (
            self.registration_deadline
            and self.lt_deadline
            and self.registration_deadline > self.lt_deadline
        ):
            errors["lt_deadline"] = "LT情報締切は参加表明締切以降にしてください。"
        if errors:
            raise ValidationError(errors)


class VketParticipation(models.Model):
    class Lifecycle(models.TextChoices):
        ACTIVE = "active", "参加中"
        DECLINED = "declined", "不参加"
        WITHDRAWN = "withdrawn", "辞退"

    class Progress(models.TextChoices):
        NOT_APPLIED = "not_applied", "未申請"
        APPLIED = "applied", "申請済み"
        STAGE_REGISTERED = "stage_registered", "ステージ登録済"
        LT_REGISTERED = "lt_registered", "LT登録済み"
        REHEARSAL = "rehearsal", "リハーサル"
        EVENT_WEEK = "event_week", "技術学術ウィーク"
        LT_MATERIAL_UPLOADED = "lt_material_uploaded", "LT資料アップロード"
        AFTER_PARTY = "after_party", "感想会"
        DONE = "done", "完了"

    collaboration = models.ForeignKey(
        VketCollaboration,
        on_delete=models.CASCADE,
        related_name="participations",
        verbose_name="コラボ",
    )
    community = models.ForeignKey(
        "community.Community",
        on_delete=models.CASCADE,
        related_name="vket_participations",
        verbose_name="集会",
    )

    lifecycle = models.CharField(
        "参加状態",
        max_length=20,
        choices=Lifecycle.choices,
        default=Lifecycle.ACTIVE,
        db_index=True,
    )
    progress = models.CharField(
        "進捗",
        max_length=30,
        choices=Progress.choices,
        default=Progress.NOT_APPLIED,
        db_index=True,
    )

    # 主催者の希望（変更不可で保持）
    requested_date = models.DateField("希望日程", null=True, blank=True)
    requested_start_time = models.TimeField("希望開始時刻", null=True, blank=True)
    requested_duration = models.PositiveIntegerField("希望開催時間（分）", null=True, blank=True)

    # 運営が確定した内容
    confirmed_date = models.DateField("確定日程", null=True, blank=True)
    confirmed_start_time = models.TimeField("確定開始時刻", null=True, blank=True)
    confirmed_duration = models.PositiveIntegerField("確定開催時間（分）", null=True, blank=True)
    schedule_adjusted_by_admin = models.BooleanField("管理者が日程調整済み", default=False)

    organizer_note = models.TextField("備考（主催者）", blank=True)
    admin_note = models.TextField("備考（運営）", blank=True)

    applied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vket_applications",
        verbose_name="申請者",
    )
    applied_at = models.DateTimeField("申請日時", null=True, blank=True)
    schedule_confirmed_at = models.DateTimeField("日程確定日時", null=True, blank=True)
    stage_registered_at = models.DateTimeField("ステージ登録日時", null=True, blank=True)
    lt_submitted_at = models.DateTimeField("LT提出日時", null=True, blank=True)
    last_acknowledged_at = models.DateTimeField("最終確認日時", null=True, blank=True)
    last_acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vket_acknowledgements",
        verbose_name="最終確認者",
    )

    # 公開同期後に紐づく（Event app）
    published_event = models.ForeignKey(
        "event.Event",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vket_participations",
        verbose_name="公開イベント",
    )

    created_at = models.DateTimeField("作成日時", auto_now_add=True)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        verbose_name = "Vket参加"
        verbose_name_plural = "Vket参加"
        db_table = "vket_participation"
        constraints = [
            models.UniqueConstraint(
                fields=["collaboration", "community"],
                name="unique_vket_participation_per_community",
            )
        ]

    def __str__(self) -> str:
        return f"{self.collaboration.name} - {self.community.name}"

    @property
    def effective_date(self):
        """確定日程があればそれを、なければ希望日程を返す"""
        return self.confirmed_date or self.requested_date

    @property
    def effective_start_time(self):
        """確定開始時刻があればそれを、なければ希望開始時刻を返す"""
        return self.confirmed_start_time or self.requested_start_time

    @property
    def effective_duration(self):
        """確定開催時間があればそれを、なければ希望開催時間を返す"""
        return self.confirmed_duration or self.requested_duration


class VketPresentation(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "下書き"
        SUBMITTED = "submitted", "提出済み"
        CONFIRMED = "confirmed", "確定"

    participation = models.ForeignKey(
        VketParticipation,
        on_delete=models.CASCADE,
        related_name="presentations",
        verbose_name="参加",
    )
    order = models.PositiveIntegerField("表示順", default=0)
    speaker = models.CharField("登壇者名", max_length=200, blank=True)
    theme = models.CharField("テーマ", max_length=200, blank=True)
    requested_start_time = models.TimeField("希望開始時刻", null=True, blank=True)
    confirmed_start_time = models.TimeField("確定開始時刻", null=True, blank=True)
    duration = models.PositiveIntegerField("発表時間（分）", default=30)
    status = models.CharField(
        "状態",
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    published_event_detail = models.ForeignKey(
        "event.EventDetail",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vket_presentations",
        verbose_name="公開イベント詳細",
    )
    created_at = models.DateTimeField("作成日時", auto_now_add=True)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        verbose_name = "Vketプレゼンテーション"
        verbose_name_plural = "Vketプレゼンテーション"
        db_table = "vket_presentation"
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return f"{self.participation} - {self.speaker or '（未入力）'}"


class VketNotice(models.Model):
    class TargetScope(models.TextChoices):
        ALL_PARTICIPANTS = "all", "全参加者"
        UNACKED = "unacked", "未確認者のみ"
        MANUAL = "manual", "手動選択"

    collaboration = models.ForeignKey(
        VketCollaboration,
        on_delete=models.CASCADE,
        related_name="notices",
        verbose_name="コラボ",
    )
    title = models.CharField("タイトル", max_length=200)
    body = models.TextField("本文")
    requires_ack = models.BooleanField("確認必須", default=False)
    target_scope = models.CharField(
        "配信対象",
        max_length=20,
        choices=TargetScope.choices,
        default=TargetScope.ALL_PARTICIPANTS,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_vket_notices",
        verbose_name="作成者",
    )
    sent_at = models.DateTimeField("送信日時", null=True, blank=True)
    created_at = models.DateTimeField("作成日時", auto_now_add=True)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        verbose_name = "Vketお知らせ"
        verbose_name_plural = "Vketお知らせ"
        db_table = "vket_notice"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.collaboration.name} - {self.title}"


class VketNoticeReceipt(models.Model):
    notice = models.ForeignKey(
        VketNotice,
        on_delete=models.CASCADE,
        related_name="receipts",
        verbose_name="お知らせ",
    )
    participation = models.ForeignKey(
        VketParticipation,
        on_delete=models.CASCADE,
        related_name="notice_receipts",
        verbose_name="参加",
    )
    acknowledged_at = models.DateTimeField("確認日時", null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vket_acks",
        verbose_name="確認者",
    )
    ack_token = models.UUIDField("確認トークン", default=uuid.uuid4, unique=True)
    created_at = models.DateTimeField("作成日時", auto_now_add=True)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        verbose_name = "Vket通知受信記録"
        verbose_name_plural = "Vket通知受信記録"
        db_table = "vket_notice_receipt"
        constraints = [
            models.UniqueConstraint(
                fields=["notice", "participation"],
                name="unique_vket_notice_receipt",
            )
        ]

    def __str__(self) -> str:
        return f"{self.notice} → {self.participation.community.name}"
