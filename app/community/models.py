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

PLATFORM_CHOICES = (
    ('PC', 'PC オンリー'),
    ('All', 'Android/PC 対応'),
    ('Android', 'Android オンリー'),
)
TAGS = (
    ('技術', '技術'),
    ('学術', '学術'),
    ('理系', '理系'),
    ('文系', '文系'),
)


class Community(models.Model):
    name = models.CharField('イベント名', max_length=100, db_index=True)
    start_from = models.DateField('イベント開始日', default=None, blank=True, null=True)
    end_at = models.DateField('イベント終了日', default=None, blank=True, null=True)
    start_time = models.TimeField('開始時刻', default='22:00')
    duration = models.IntegerField('開催時間', default=60, help_text='単位は分')
    weekday = models.CharField('曜日', max_length=5, choices=WEEKDAY_CHOICES)
    frequency = models.CharField('開催周期', max_length=100)
    organizers = models.CharField('主催・副主催', max_length=200)
    group_url = models.URLField('VRChatグループURL', blank=True)
    organizer_url = models.URLField('VRChat主催プロフィールURL', blank=True)
    discord = models.URLField('Discord', blank=True)
    twitter_hashtag = models.CharField('Twitterハッシュタグ', max_length=100, blank=True)
    poster_image = models.ImageField('ポスター', upload_to='poster/', blank=True)
    description = models.TextField('イベント紹介', default='', blank=True)
    platform = models.CharField('対応プラットフォーム', max_length=10, choices=PLATFORM_CHOICES, default='All')
    tags = models.JSONField('タグ', max_length=10, choices=TAGS, default=list)

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
