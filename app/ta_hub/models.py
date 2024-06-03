from django.db import models


class Community(models.Model):
    WEEKDAY_CHOICES = (
        ('Sun', '日曜日'),
        ('Mon', '月曜日'),
        ('Tue', '火曜日'),
        ('Wed', '水曜日'),
        ('Thu', '木曜日'),
        ('Fri', '金曜日'),
        ('Sat', '土曜日'),
        ('Other', 'その他')
    )

    name = models.CharField('イベント名', max_length=100)
    start_time = models.TimeField('開始時刻')
    duration = models.DurationField('開催時間')
    weekday = models.CharField('曜日', max_length=5, choices=WEEKDAY_CHOICES)
    frequency = models.CharField('開催周期', max_length=100)
    organizers = models.CharField('主催・副主催', max_length=200)
    vrchat_group = models.URLField('VRChatグループ', blank=True)
    discord = models.URLField('Discord', blank=True)
    twitter_hashtag = models.CharField('Twitterハッシュタグ', max_length=100, blank=True)
    poster_image = models.URLField('ポスター画像（URL）', blank=True)
    description = models.TextField('イベント紹介')

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = '集会'
        verbose_name_plural = '集会'


class Event(models.Model):
    meeting = models.ForeignKey(Community, on_delete=models.CASCADE, related_name='events', verbose_name='集会')
    date = models.DateField('開催日')
    youtube_url = models.URLField('YouTube URL', blank=True)
    materials_url = models.URLField('資料 URL', blank=True)
    speakers = models.CharField('登壇者', max_length=200)
    theme = models.CharField('テーマ', max_length=100)
    overview = models.TextField('概要')

    def __str__(self):
        return f"{self.meeting.name} - {self.date}"

    class Meta:
        verbose_name = 'イベント'
        verbose_name_plural = 'イベント'
