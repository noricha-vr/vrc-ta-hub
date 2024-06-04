from django.db import models
from community.models import Community, WEEKDAY_CHOICES


class Event(models.Model):
    meeting = models.ForeignKey(Community, on_delete=models.CASCADE, related_name='events', verbose_name='集会')
    date = models.DateField('開催日')
    start_time = models.TimeField('開始時刻')
    duration = models.IntegerField('開催時間', default=60, help_text='単位は分')
    weekday = models.CharField('曜日', max_length=5, choices=WEEKDAY_CHOICES)
    youtube_url = models.URLField('YouTube URL', blank=True)
    materials_url = models.URLField('資料 URL', blank=True)
    speakers = models.CharField('登壇者', max_length=200)
    theme = models.CharField('テーマ', max_length=100)
    overview = models.TextField('概要')

    def __str__(self):
        return f"{self.meeting.name} - {self.date} - {self.speakers}『{self.theme}』"

    class Meta:
        verbose_name = 'イベント'
        verbose_name_plural = 'イベント'
        db_table = 'event'
