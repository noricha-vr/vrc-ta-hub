"""Grok/X Search を使った集会活動監視の監査モデル。"""

from django.db import models


class CommunityActivityState(models.Model):
    """集会ごとの最新活動状態と自動非表示の進行状況。"""

    class Status(models.TextChoices):
        UNKNOWN = "unknown", "未確認"
        ACTIVE = "active", "活動中"
        SUSPECTED_INACTIVE = "suspected_inactive", "停止の可能性"
        INACTIVE = "inactive", "活動停止"
        UNCERTAIN = "uncertain", "判定不能"
        ERROR = "error", "確認エラー"

    community = models.OneToOneField(
        "community.Community",
        on_delete=models.CASCADE,
        related_name="activity_state",
        verbose_name="集会",
    )
    monitoring_enabled = models.BooleanField(
        "自動監視を有効にする",
        default=True,
        help_text="季節開催・不定期開催など、自動判定に向かない集会はOFFにしてください。",
    )
    status = models.CharField(
        "活動状態",
        max_length=24,
        choices=Status.choices,
        default=Status.UNKNOWN,
        db_index=True,
    )
    consecutive_inactive_checks = models.PositiveSmallIntegerField(
        "連続非活動判定回数",
        default=0,
    )
    inactive_detected_at = models.DateTimeField("最初の非活動検知日時", null=True, blank=True)
    warning_sent_at = models.DateTimeField("Discord警告送信日時", null=True, blank=True)
    auto_hidden_at = models.DateTimeField("自動非表示日時", null=True, blank=True)
    last_checked_at = models.DateTimeField("最終確認日時", null=True, blank=True, db_index=True)
    check_started_at = models.DateTimeField(
        "確認処理開始日時",
        null=True,
        blank=True,
        help_text="Cloud Runの多重実行を防ぐためのリース。異常終了時は一定時間後に再取得されます。",
    )
    last_activity_at = models.DateField("X上の最終活動日", null=True, blank=True)
    last_signal = models.CharField("最新判定シグナル", max_length=32, blank=True, default="")
    last_confidence = models.DecimalField(
        "最新判定の信頼度",
        max_digits=4,
        decimal_places=3,
        default=0,
    )
    last_reason = models.TextField("最新判定理由", blank=True, default="")
    last_evidence = models.JSONField("最新根拠", default=list, blank=True)
    last_response_id = models.CharField("xAIレスポンスID", max_length=100, blank=True, default="")
    last_model_name = models.CharField("xAIモデル", max_length=100, blank=True, default="")
    last_cost_in_usd_ticks = models.BigIntegerField("最新確認コスト(ticks)", null=True, blank=True)
    created_at = models.DateTimeField("作成日時", auto_now_add=True)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        db_table = "community_activity_state"
        verbose_name = "集会活動監視状態"
        verbose_name_plural = "集会活動監視状態"
        indexes = [
            models.Index(fields=["monitoring_enabled", "last_checked_at"], name="comm_act_due_idx"),
        ]

    def __str__(self):
        return f"{self.community.name}: {self.get_status_display()}"


class CommunityActivityCheck(models.Model):
    """Grokによる1回ごとの判定履歴。"""

    class Result(models.TextChoices):
        ACTIVE = "active", "活動中"
        INACTIVE = "inactive", "活動停止"
        UNCERTAIN = "uncertain", "判定不能"
        ERROR = "error", "エラー"

    class Action(models.TextChoices):
        NONE = "none", "変更なし"
        WARNED = "warned", "Discord警告"
        HIDDEN = "hidden", "自動非表示"

    community = models.ForeignKey(
        "community.Community",
        on_delete=models.CASCADE,
        related_name="activity_checks",
        verbose_name="集会",
    )
    result = models.CharField("判定", max_length=16, choices=Result.choices, db_index=True)
    signal = models.CharField("判定シグナル", max_length=32, blank=True, default="")
    confidence = models.DecimalField("信頼度", max_digits=4, decimal_places=3, default=0)
    last_activity_at = models.DateField("X上の最終活動日", null=True, blank=True)
    reason = models.TextField("判定理由", blank=True, default="")
    evidence = models.JSONField("根拠", default=list, blank=True)
    response_id = models.CharField("xAIレスポンスID", max_length=100, blank=True, default="")
    model_name = models.CharField("xAIモデル", max_length=100, blank=True, default="")
    cost_in_usd_ticks = models.BigIntegerField("コスト(ticks)", null=True, blank=True)
    action = models.CharField(
        "実行した処理",
        max_length=16,
        choices=Action.choices,
        default=Action.NONE,
    )
    created_at = models.DateTimeField("確認日時", auto_now_add=True, db_index=True)

    class Meta:
        db_table = "community_activity_check"
        verbose_name = "集会活動確認履歴"
        verbose_name_plural = "集会活動確認履歴"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["community", "created_at"], name="comm_act_hist_idx"),
            models.Index(fields=["result", "created_at"], name="comm_act_result_idx"),
        ]

    def __str__(self):
        checked_on = self.created_at.date().isoformat() if self.created_at else "未保存"
        return f"{self.community.name}: {self.get_result_display()} ({checked_on})"
