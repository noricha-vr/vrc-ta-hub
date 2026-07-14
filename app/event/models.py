import logging
import os
import re
import uuid
from datetime import datetime, timedelta
from typing import Optional

import filetype
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from community.models import Community, WEEKDAY_CHOICES

logger = logging.getLogger(__name__)


def slide_file_upload_to(instance, filename):
    """スライドPDFを `slide/<uuid>.pdf` に正規化する。

    元のファイル名（個人情報や推測可能名を含む可能性）を公開URLに出さず、
    R2上での衝突も避ける。拡張子はストレージ側のContent-Type判定用に `.pdf` を強制。
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext != '.pdf':
        ext = '.pdf'
    return f'slide/{uuid.uuid4().hex}{ext}'


def validate_pdf_file(value):
    """PDFファイルのバリデーション（拡張子 + マジックバイト検証）

    Args:
        value: アップロードされたファイルオブジェクト

    Raises:
        ValidationError: PDFファイルでない場合
    """
    if value:
        # 拡張子チェック
        if not value.name.lower().endswith('.pdf'):
            raise ValidationError('PDFファイルのみアップロード可能です。')

        # 拡張子偽装（.pdf だが実体はHTML/JSなど）を防ぐため、PDFのマジックナンバーを確認
        try:
            header = value.read(5)
            value.seek(0)
        except Exception:
            raise ValidationError('PDFファイルのみアップロード可能です。')

        if header != b'%PDF-':
            raise ValidationError('PDFファイルのみアップロード可能です。')

        # マジックバイト検証
        # filetypeライブラリが必要とする最小バイト数を読み取り
        header = value.read(262)
        value.seek(0)  # ファイルポインタをリセット

        kind = filetype.guess(header)
        if kind is None or kind.mime != 'application/pdf':
            raise ValidationError('有効なPDFファイルではありません。ファイルの内容を確認してください。')


class RecurrenceRule(models.Model):
    """定期イベントのルール"""
    FREQUENCY_CHOICES = [
        ('WEEKLY', '毎週'),
        ('MONTHLY_BY_DATE', '毎月（日付指定）'),
        ('MONTHLY_BY_WEEK', '毎月（第N曜日）'),
        ('OTHER', 'その他（自由記述）'),
    ]
    
    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name='recurrence_rules', verbose_name='集会', null=True, blank=True)
    frequency = models.CharField(max_length=20, choices=FREQUENCY_CHOICES, verbose_name='頻度')
    interval = models.IntegerField(default=1, verbose_name='間隔')  # 何週間/月ごとか
    week_of_month = models.IntegerField(null=True, blank=True, verbose_name='第N週')  # MONTHLY_BY_WEEKの場合
    custom_rule = models.TextField(null=True, blank=True, verbose_name='カスタムルール')  # OTHERの場合の自由記述
    start_date = models.DateField(null=True, blank=True, verbose_name='起点日',
                                  help_text='定期イベントの起点となる日付。隔週の場合はこの日付を基準に計算されます。')
    end_date = models.DateField(null=True, blank=True, verbose_name='終了日')
    # 冪等性保証用: generate_recurring_events 実行時、最後に生成完了した日付を記録する。
    # 既存の Event.objects.filter(...).exists() チェックで重複は防げているが、
    # 進捗トラッキングとリカバリ判断（どこまで生成済みか）を可視化するために保持する。
    last_generated_date = models.DateField(
        null=True,
        blank=True,
        verbose_name='最終生成日',
        help_text='最後にイベント生成した日付（冪等性保証・進捗トラッキング用）',
    )

    # 管理用フィールド
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = '定期ルール'
        verbose_name_plural = '定期ルール'
    
    def __str__(self):
        community_name = self.community.name if self.community else "未設定"
        if self.frequency == 'OTHER':
            return f"{community_name} - {self.custom_rule[:50]}..."
        return f"{community_name} - {dict(self.FREQUENCY_CHOICES).get(self.frequency, '')}"
    
    def is_occurrence_date(self, check_date):
        """指定された日付がこのルールに従う開催日かどうかを判定"""
        if not self.start_date:
            return True  # 起点日が設定されていない場合は常にTrue
        
        if self.end_date and check_date > self.end_date:
            return False  # 終了日を過ぎている場合はFalse
        
        if self.frequency == 'WEEKLY':
            # 曜日が一致しているかチェック
            if check_date.weekday() != self.start_date.weekday():
                return False
            
            # 起点日からの経過週数を計算
            days_diff = (check_date - self.start_date).days
            weeks_diff = days_diff // 7
            
            # intervalごとに開催されるかチェック
            return weeks_diff % self.interval == 0
        
        # その他の頻度の場合は現在の実装を維持
        return True
    
    def get_next_occurrence(self, after_date):
        """指定日以降の次回開催日を取得"""
        if not self.start_date:
            return None
        
        if self.frequency == 'WEEKLY':
            # 起点日の曜日を取得
            start_weekday = self.start_date.weekday()
            
            # after_dateを起点日の曜日に合わせる
            current_date = after_date
            while current_date.weekday() != start_weekday:
                current_date += timedelta(days=1)
            
            # 開催日になるまで進める
            while not self.is_occurrence_date(current_date):
                current_date += timedelta(days=7)
            
            return current_date
        
        return None
    
    def delete_future_events(self, delete_from_date=None):
        """この定期ルールに関連する未来のイベントを削除
        
        Args:
            delete_from_date: この日付以降のイベントを削除（指定しない場合は今日以降）
        
        Returns:
            削除されたイベント数
        """
        if delete_from_date is None:
            delete_from_date = timezone.now().date()
        
        # このルールに関連するマスターイベントを取得
        master_events = Event.objects.filter(
            recurrence_rule=self,
            is_recurring_master=True
        )
        
        deleted_count = 0
        
        for master_event in master_events:
            # マスターイベント自体が未来の場合
            if master_event.date >= delete_from_date:
                # マスターイベントとその全てのインスタンスを削除
                instance_count = master_event.recurring_instances.count()
                master_event.delete()  # カスケード削除でインスタンスも削除される
                deleted_count += instance_count + 1
            else:
                # マスターイベントは過去だが、インスタンスに未来のものがある場合
                future_instances = master_event.recurring_instances.filter(
                    date__gte=delete_from_date
                )
                deleted_count += future_instances.count()
                future_instances.delete()
        
        return deleted_count
    
    def delete(self, *args, **kwargs):
        """定期ルールを削除する際のカスタム処理
        
        delete_future_events: 未来のイベントも削除するかどうか（デフォルト：True）
        """
        delete_future_events = kwargs.pop('delete_future_events', True)
        
        if delete_future_events:
            deleted_count = self.delete_future_events()
            logger.info(
                "Deleted %s future events related to this recurrence rule.",
                deleted_count,
            )
        
        super().delete(*args, **kwargs)


class Event(models.Model):
    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name='events', verbose_name='集会')
    date = models.DateField('開催日', db_index=True)
    start_time = models.TimeField('開始時刻', default='22:00')
    duration = models.IntegerField('開催時間（分）', default=60)
    weekday = models.CharField('曜日', max_length=5, choices=WEEKDAY_CHOICES, blank=True)
    google_calendar_event_id = models.CharField('GoogleカレンダーイベントID', max_length=255, blank=True, null=True)

    # 定期イベント関連フィールド
    recurrence_rule = models.ForeignKey(RecurrenceRule, null=True, blank=True, on_delete=models.SET_NULL, verbose_name='定期ルール')
    is_recurring_master = models.BooleanField(default=False, verbose_name='定期イベントの親')
    recurring_master = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='recurring_instances', verbose_name='親イベント')

    # LT申請機能
    accepts_lt_application = models.BooleanField('LT申し込み受付', default=True)

    class Meta:
        verbose_name = 'イベント'
        verbose_name_plural = 'イベント'
        db_table = 'event'
        constraints = [
            models.UniqueConstraint(
                fields=['community', 'date', 'start_time'],
                name='event_unique_community_date_start_time',
            ),
        ]

    def __str__(self):
        return f"{self.community.name} - {self.date} - {self.start_time}"

    @property
    def end_time(self):
        start_datetime = datetime.combine(self.date, self.start_time)
        end_datetime = start_datetime + timedelta(minutes=self.duration)
        return end_datetime.time()
    
    @property
    def is_recurring_instance(self):
        """定期イベントのインスタンスかどうか"""
        return self.recurring_master is not None
    
    @property
    def start_at(self):
        """開始日時"""
        if self.start_time:
            return datetime.combine(self.date, self.start_time)
        return datetime.combine(self.date, datetime.min.time())


class EventDetailQuerySet(models.QuerySet):
    """QuerySet レベルで ``.delete()`` を soft delete に倒す。

    ``EventDetail.objects.filter(...).delete()`` のような既存コードを
    ハードコードのまま soft delete 化するためのフック。物理削除したい場合は
    ``hard_delete()`` を呼ぶか、``all_objects`` 側を経由する。
    """

    def delete(self):
        # update() は save() を発火させない高速 UPDATE。auto_now を伴う
        # updated_at は明示的に揃え、復元時の操作とも整合させる。
        now = timezone.now()
        return self.update(deleted_at=now, updated_at=now)

    def hard_delete(self):
        return super().delete()

    def restore(self):
        return self.update(deleted_at=None, updated_at=timezone.now())


class EventDetailManager(models.Manager):
    """生存中（soft delete されていない）EventDetail のみを返すマネージャ。

    既存コード（views / API / 集計）の ``EventDetail.objects.filter(...)`` の挙動を
    変えないために、デフォルトマネージャの段階で ``deleted_at IS NULL`` で絞る。
    削除済みレコードを含めたい場合は ``EventDetail.all_objects`` を使う。
    """

    def get_queryset(self):
        return EventDetailQuerySet(self.model, using=self._db).filter(deleted_at__isnull=True)


class EventDetailAllManager(models.Manager):
    """削除済みを含む全 EventDetail を返すマネージャ。

    QuerySet レベルでの ``.delete()`` も soft delete として動作するよう
    EventDetailQuerySet を使用する。本当に物理削除したい場合は
    ``EventDetail.all_objects.filter(...).hard_delete()`` を呼ぶ。
    """

    def get_queryset(self):
        return EventDetailQuerySet(self.model, using=self._db)


class EventDetail(models.Model):
    TYPE_CHOICES = [
        ('LT', '発表'),
        ('SPECIAL', '特別企画'),
        ('BLOG', 'ブログ'),
    ]

    STATUS_CHOICES = [
        ('pending', '承認待ち'),
        ('approved', '承認済み'),
        ('rejected', '却下'),
    ]

    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    updated_at = models.DateTimeField('更新日時', auto_now=True)
    # NULL = 生存。タイムスタンプ入り = soft delete 済み。
    # 関連する TweetQueue / analytics を孤立させずに残し、復元できるようにする。
    deleted_at = models.DateTimeField('削除日時', null=True, blank=True, db_index=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='details', verbose_name='イベント')
    detail_type = models.CharField('タイプ', max_length=10, choices=TYPE_CHOICES, default='LT', db_index=True)
    start_time = models.TimeField('開始時刻', default='22:00', db_index=True)
    duration = models.IntegerField('発表の持ち時間（分）', default=30)
    youtube_url = models.URLField('YouTube URL', blank=True, null=True)
    slide_url = models.URLField('スライド URL', blank=True, null=True)
    slide_file = models.FileField('スライド', blank=True, null=True, upload_to=slide_file_upload_to, validators=[validate_pdf_file])
    thumbnail_image = models.ImageField(
        'サムネイル画像',
        blank=True,
        null=True,
        upload_to='thumbnail/',
        help_text='記事ページの上部に表示する画像',
    )
    speaker = models.CharField('発表者', max_length=200, blank=True, default='',
                               help_text="VRChat表示名が望ましい。ただし、表記揺れはそのうち勝手に調整します",
                               db_index=True)
    theme = models.CharField('テーマ', max_length=100, blank=True, default='', db_index=True)
    h1 = models.CharField('タイトル(H1)', max_length=255, blank=True, default='', db_index=True)
    contents = models.TextField('内容', blank=True, default='')
    meta_description = models.CharField(
        'メタディスクリプション', max_length=255, blank=True, default='')

    # LT申請関連フィールド
    status = models.CharField(
        '申請状態', max_length=10, choices=STATUS_CHOICES, default='approved', db_index=True
    )
    applicant = models.ForeignKey(
        'user_account.CustomUser', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='lt_applications', verbose_name='申請者'
    )
    rejection_reason = models.TextField('却下理由', blank=True, default='')
    additional_info = models.TextField(
        '追加情報',
        blank=True,
        default='',
        help_text='登壇者が入力した追加情報'
    )

    # soft delete 用マネージャ。`objects` は既存挙動互換（生存のみ）、
    # `all_objects` は削除済みを含む全件。Django は宣言順の最初の Manager を
    # default_manager として扱うため、`objects` を先に置く。
    objects = EventDetailManager()
    all_objects = EventDetailAllManager()

    class Meta:
        verbose_name = 'イベント詳細'
        verbose_name_plural = 'イベント詳細'
        db_table = 'event_detail'
        indexes = [
            models.Index(fields=['event', 'start_time']),
            models.Index(fields=['event', '-start_time']),
            models.Index(fields=['detail_type']),
        ]

    def __str__(self):
        return f"{self.event} - {self.theme} - {self.speaker}"

    def soft_delete(self):
        """deleted_at を現在時刻でマークする（論理削除）。

        既存の ``post_delete`` ハンドラ（キャッシュ無効化・リマインダー同期等）が
        UI からの「消えたように見える」状態に追従するよう、論理削除でも post_delete
        シグナルを発火させる。物理削除しないため Django 標準の post_delete は
        送られないので、手動で送る。
        """
        from django.db.models.signals import post_delete

        self.deleted_at = timezone.now()
        self.save(update_fields=['deleted_at'])
        post_delete.send(sender=type(self), instance=self, using=self._state.db)

    def restore(self):
        """論理削除を取り消し、生存状態に戻す。"""
        self.deleted_at = None
        self.save(update_fields=['deleted_at'])

    def delete(self, using=None, keep_parents=False):
        """既存コードの ``instance.delete()`` を soft delete に置き換える。

        物理削除が本当に必要な場合は ``hard_delete()`` を明示的に呼ぶこと。
        """
        self.soft_delete()

    def hard_delete(self, using=None, keep_parents=False):
        """物理削除（DB から行を消す）を行う。

        通常は使わない。マイグレーション・データ移行・管理コマンドで
        本当に row を消したい場合のみ使用する。
        """
        return super().delete(using=using, keep_parents=keep_parents)

    @property
    def title(self):
        return self.h1 if self.h1 else self.theme

    @property
    def end_time(self):
        start_datetime = datetime.combine(self.event.date, self.start_time)
        end_datetime = start_datetime + timedelta(minutes=self.duration)
        return end_datetime.time()

    @property
    def video_id(self) -> Optional[str]:
        if self.youtube_url:
            # 正規表現を使ってvideo_idを抽出
            match = re.search(r'(?:v=|\/)([0-9A-Za-z_-]{11}).*', self.youtube_url)
            if match:
                return match.group(1)
        return None


class MaterialUploadReminderLog(models.Model):
    """発表資料アップロード依頼の送信・除外結果を記録する。"""

    class Status(models.TextChoices):
        SENT = "sent", "送信済み"
        SKIPPED_BY_NOTE = "skipped_by_note", "備考で除外"

    event_detail = models.OneToOneField(
        EventDetail,
        on_delete=models.CASCADE,
        related_name="material_upload_reminder_log",
        verbose_name="イベント詳細",
    )
    status = models.CharField("状態", max_length=30, choices=Status.choices, db_index=True)
    reason = models.TextField("理由", blank=True, default="")
    matched_intent = models.CharField("一致した意図", max_length=100, blank=True, default="")
    confidence = models.CharField("信頼度", max_length=20, blank=True, default="")
    sent_at = models.DateTimeField("送信日時", null=True, blank=True)
    follow_up_sent_at = models.DateTimeField("1週間後リマインド送信日時", null=True, blank=True, db_index=True)
    created_at = models.DateTimeField("作成日時", auto_now_add=True)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        verbose_name = "発表資料アップロード依頼ログ"
        verbose_name_plural = "発表資料アップロード依頼ログ"
        db_table = "event_material_upload_reminder_log"
