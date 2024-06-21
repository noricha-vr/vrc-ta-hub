from datetime import datetime, timedelta

from django.db import models

from community.models import Community, WEEKDAY_CHOICES


class Event(models.Model):
    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name='events', verbose_name='集会')
    date = models.DateField('開催日', db_index=True)
    start_time = models.TimeField('開始時刻', default='22:00')
    duration = models.IntegerField('開催時間（分）', default=60)
    weekday = models.CharField('曜日', max_length=5, choices=WEEKDAY_CHOICES, blank=True)

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


class EventDetail(models.Model):
    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    updated_at = models.DateTimeField('更新日時', auto_now=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='details', verbose_name='イベント')
    start_time = models.TimeField('開始時刻', default='22:00')
    duration = models.IntegerField('発表時間（分）', default=30)
    youtube_url = models.URLField('YouTube URL', blank=True, null=True)
    slide_url = models.URLField('スライド URL', blank=True, null=True)
    slide_file = models.FileField('スライド', blank=True, null=True, upload_to='slide/')
    speaker = models.CharField('発表者', max_length=200, blank=True, default='', db_index=True)
    theme = models.CharField('テーマ', max_length=100, blank=True, default='', db_index=True)
    h1 = models.CharField('タイトル(H1)', max_length=255, blank=True, default='', db_index=True)
    contents = models.TextField('内容', blank=True, default='')

    class Meta:
        verbose_name = 'イベント詳細'
        verbose_name_plural = 'イベント詳細'
        db_table = 'event_detail'

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
