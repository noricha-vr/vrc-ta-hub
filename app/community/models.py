from django.db import models
from datetime import timedelta
from django.utils import timezone
from ta_hub.libs import resize_and_convert_image

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
    ('PC', 'PC'),
    ('All', 'Android・PC'),
    ('Android', 'Android'),
)
TAGS = (
    ('tech', '技術'),
    ('academic', '学術'),
)

STATUS_CHOICES = (
    ('pending', '承認待ち'),
    ('approved', '承認済み'),
    ('rejected', '非承認'),
)


class Community(models.Model):
    custom_user = models.ForeignKey('account.CustomUser', on_delete=models.CASCADE, verbose_name='ユーザー',
                                    default=None, blank=True, null=True)
    name = models.CharField('集会名', max_length=100, db_index=True)
    created_at = models.DateField('開始日', default=timezone.now, blank=True, null=True, db_index=True)
    updated_at = models.DateField('更新日', auto_now=True, db_index=True)
    end_at = models.DateField('終了日', default=None, blank=True, null=True)
    start_time = models.TimeField('開始時刻', default='22:00', db_index=True)
    duration = models.IntegerField('開催時間', default=60, help_text='単位は分')
    weekdays = models.JSONField('曜日', default=list, blank=True)  # JSONFieldに変更
    frequency = models.CharField('開催周期', max_length=100)
    organizers = models.CharField('主催・副主催', max_length=200)
    group_url = models.URLField('VRChatグループURL', blank=True)
    organizer_url = models.URLField('主催プロフィールURL', blank=True)
    sns_url = models.URLField('SNS', blank=True)
    discord = models.URLField('Discord', blank=True)
    twitter_hashtag = models.CharField('Twitterハッシュタグ', max_length=100, blank=True)
    poster_image = models.ImageField('ポスター', upload_to='poster/', blank=True)
    description = models.TextField('イベント紹介', default='', blank=True)
    platform = models.CharField('対応プラットフォーム', max_length=10, choices=PLATFORM_CHOICES, default='All')
    tags = models.JSONField('タグ', max_length=10, default=list)
    status = models.CharField('承認状態', max_length=20, choices=STATUS_CHOICES, default='pending', db_index=True)

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

    @property
    def get_sns_display(self):
        if self.sns_url:
            sns_parts = self.sns_url.split('/')
            return f"@{sns_parts[-1]}"
        return None

    @property
    def is_accepted(self):
        return self.status == 'approved'

    def save(self, *args, **kwargs):
        # poster_image をリサイズしてJPEGに変換
        resize_and_convert_image(self.poster_image, max_size=1000, output_format='JPEG')
        super().save(*args, **kwargs)
