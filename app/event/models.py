from django.db import models
from community.models import Community, WEEKDAY_CHOICES
from datetime import datetime, timedelta


class Event(models.Model):
    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name='events', verbose_name='集会')
    date = models.DateField('開催日', db_index=True)
    start_time = models.TimeField('開始時刻', default='22:00')
    duration = models.IntegerField('開催時間', default=60, help_text='単位は分')
    weekday = models.CharField('曜日', max_length=5, choices=WEEKDAY_CHOICES, blank=True)
    youtube_url = models.URLField('YouTube URL', blank=True)
    materials_url = models.URLField('資料 URL', blank=True)
    speakers = models.CharField('登壇者', max_length=200)
    theme = models.CharField('テーマ', max_length=100)
    overview = models.TextField('概要')

    class Meta:
        verbose_name = 'イベント'
        verbose_name_plural = 'イベント'
        db_table = 'event'
        unique_together = ('community', 'date', 'start_time')

    def __str__(self):
        return f"{self.community.name} - {self.date} - {self.speakers}『{self.theme}』"

    @property
    def end_time(self):
        start_datetime = datetime.combine(self.date, self.start_time)
        end_datetime = start_datetime + timedelta(minutes=self.duration)
        return end_datetime.time()
