from django.db import models
from django.utils import timezone

from community.models import Community
from twitter.scheduling import default_scheduled_at


class TwitterTemplate(models.Model):
    name = models.CharField('テンプレート名', max_length=255, default="")
    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name='twitter_template')
    template = models.TextField('テンプレート',
                                help_text="利用可能な変数: {event_name}, {date}, {time}, {speaker}, {theme}")

    def __str__(self):
        return f"X Template for {self.community.name}"

    class Meta:
        db_table = 'twitter_template'


class TweetQueue(models.Model):
    """X 自動投稿キュー

    集会承認・LT/特別回承認時にシグナルでキューに追加され、
    Cloud Scheduler (30分ごと) からのリクエストで投稿対象だけが処理される。
    """

    TWEET_TYPE_CHOICES = [
        ('new_community', '新規集会'),
        ('lt', 'LT告知'),
        ('special', '特別回告知'),
        ('daily_reminder', '当日リマインド'),
        ('slide_share', 'スライド/記事共有'),
    ]
    STATUS_CHOICES = [
        ('generating', '生成中'),
        ('generation_failed', '生成失敗'),
        ('ready', '投稿待ち'),
        ('posted', '投稿済み'),
        ('skipped', 'スキップ'),
        ('failed', '投稿失敗'),
    ]

    tweet_type = models.CharField(
        '種別', max_length=20, choices=TWEET_TYPE_CHOICES,
    )
    community = models.ForeignKey(
        'community.Community', on_delete=models.CASCADE, related_name='tweet_queues',
    )
    event = models.ForeignKey(
        'event.Event', on_delete=models.CASCADE, null=True, blank=True, related_name='tweet_queues',
    )
    event_detail = models.ForeignKey(
        'event.EventDetail', on_delete=models.CASCADE, null=True, blank=True, related_name='tweet_queues',
    )
    generated_text = models.TextField('生成テキスト', blank=True)
    image_url = models.URLField('画像URL', blank=True, help_text='投稿に添付する画像のURL（R2等）')
    status = models.CharField(
        '状態', max_length=20, choices=STATUS_CHOICES, default='generating',
    )
    tweet_id = models.CharField('ポストID', max_length=50, blank=True)
    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    scheduled_at = models.DateTimeField('予約日時')
    posted_at = models.DateTimeField('投稿日時', null=True, blank=True)
    error_message = models.TextField('エラーメッセージ', blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'ポストキュー'
        verbose_name_plural = 'ポストキュー'
        db_table = 'tweet_queue'
        constraints = [
            models.UniqueConstraint(
                fields=['event', 'tweet_type'],
                condition=models.Q(tweet_type='daily_reminder'),
                name='unique_daily_reminder_per_event',
            ),
        ]

    def __str__(self):
        return f"[{self.get_tweet_type_display()}] {self.community.name} - {self.get_status_display()}"

    def save(self, *args, **kwargs):
        if self.scheduled_at is None:
            self.scheduled_at = default_scheduled_at(
                tweet_type=self.tweet_type,
                event=self.event,
                base_datetime=self.created_at or timezone.now(),
            )
        super().save(*args, **kwargs)
