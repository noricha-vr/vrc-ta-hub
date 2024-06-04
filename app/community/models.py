from django.db import models
from datetime import timedelta
from django.utils import timezone

# Create your models here.
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


class Community(models.Model):
    name = models.CharField('イベント名', max_length=100)
    start_time = models.TimeField('開始時刻')
    duration = models.IntegerField('開催時間', default=60, help_text='単位は分')
    weekday = models.CharField('曜日', max_length=5, choices=WEEKDAY_CHOICES)
    frequency = models.CharField('開催周期', max_length=100)
    organizers = models.CharField('主催・副主催', max_length=200)
    vrchat_group = models.URLField('VRChatグループ', blank=True)
    discord = models.URLField('Discord', blank=True)
    twitter_hashtag = models.CharField('Twitterハッシュタグ', max_length=100, blank=True)
    poster_image = models.ImageField('ポスター', blank=True)
    description = models.TextField('イベント紹介')

    # platform = models.CharField('プラットフォーム', max_length=100)
    # genre = models.CharField('ジャンル', max_length=100)

    class Meta:
        verbose_name = '集会'
        verbose_name_plural = '集会'
        db_table = 'community'

    def __str__(self):
        return self.name

    @property
    def end_time(self):
        return (timezone.datetime.combine(timezone.datetime.today(), self.start_time) + timedelta(
            minutes=self.duration)).time()
