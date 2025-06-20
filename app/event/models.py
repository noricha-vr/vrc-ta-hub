import re
from datetime import datetime, timedelta
from typing import Optional

from django.core.exceptions import ValidationError
from django.db import models

from community.models import Community, WEEKDAY_CHOICES


def validate_pdf_file(value):
    """PDFファイルのバリデーション"""
    if value:
        if not value.name.lower().endswith('.pdf'):
            raise ValidationError('PDFファイルのみアップロード可能です。')


class RecurrenceRule(models.Model):
    """定期イベントのルール"""
    FREQUENCY_CHOICES = [
        ('WEEKLY', '毎週'),
        ('MONTHLY_BY_DATE', '毎月（日付指定）'),
        ('MONTHLY_BY_WEEK', '毎月（第N曜日）'),
        ('OTHER', 'その他（自由記述）'),
    ]
    
    frequency = models.CharField(max_length=20, choices=FREQUENCY_CHOICES, verbose_name='頻度')
    interval = models.IntegerField(default=1, verbose_name='間隔')  # 何週間/月ごとか
    week_of_month = models.IntegerField(null=True, blank=True, verbose_name='第N週')  # MONTHLY_BY_WEEKの場合
    custom_rule = models.TextField(null=True, blank=True, verbose_name='カスタムルール')  # OTHERの場合の自由記述
    end_date = models.DateField(null=True, blank=True, verbose_name='終了日')
    
    # 管理用フィールド
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = '定期ルール'
        verbose_name_plural = '定期ルール'
    
    def __str__(self):
        if self.frequency == 'OTHER':
            return f"{self.custom_rule[:50]}..."
        return dict(self.FREQUENCY_CHOICES).get(self.frequency, '')


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

    class Meta:
        verbose_name = 'イベント'
        verbose_name_plural = 'イベント'
        db_table = 'event'
        unique_together = ('community', 'date', 'start_time')

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


class EventDetail(models.Model):
    TYPE_CHOICES = [
        ('LT', 'LT（発表）'),
        ('SPECIAL', '特別企画'),
        ('BLOG', 'ブログ'),
    ]

    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    updated_at = models.DateTimeField('更新日時', auto_now=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='details', verbose_name='イベント')
    detail_type = models.CharField('タイプ', max_length=10, choices=TYPE_CHOICES, default='LT', db_index=True)
    start_time = models.TimeField('開始時刻', default='22:00', db_index=True)
    duration = models.IntegerField('発表時間（分）', default=30)
    youtube_url = models.URLField('YouTube URL', blank=True, null=True)
    slide_url = models.URLField('スライド URL', blank=True, null=True)
    slide_file = models.FileField('スライド', blank=True, null=True, upload_to='slide/', validators=[validate_pdf_file])
    speaker = models.CharField('発表者', max_length=200, blank=True, default='',
                               help_text="VRChat表示名が望ましい。ただし、表記揺れはそのうち勝手に調整します",
                               db_index=True)
    theme = models.CharField('テーマ', max_length=100, blank=True, default='', db_index=True)
    h1 = models.CharField('タイトル(H1)', max_length=255, blank=True, default='', db_index=True)
    contents = models.TextField('内容', blank=True, default='')
    meta_description = models.CharField(
        'メタディスクリプション', max_length=255, blank=True, default='')

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
