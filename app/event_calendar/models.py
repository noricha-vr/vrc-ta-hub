# app/event_calendar/models.py

from django.core.exceptions import ValidationError
from django.db import models

from community.models import Community


class CalendarEntry(models.Model):
    ANDROID_SUPPORT_CHOICES = [
        ('PC_ONLY', 'PCオンリー'),
        ('PC_ANDROID', 'PC/Android両対応（Android対応）'),
        ('ANDROID_ONLY', 'Android オンリー'),
    ]

    EVENT_GENRE_CHOICES = [
        ('AVATAR_FITTING', 'アバター試着会'),
        ('MODIFIED_AVATAR_MEETUP', '改変アバター交流会'),
        ('OTHER_MEETUP', 'その他交流会'),
        ('VR_DRINKING_PARTY', 'VR飲み会'),
        ('STORE_EVENT', '店舗系イベント'),
        ('MUSIC_EVENT', '音楽系イベント'),
        ('ACADEMIC_EVENT', '学術系イベント'),
        ('ROLEPLAY', 'ロールプレイ'),
        ('BEGINNER_EVENT', '初心者向けイベント'),
        ('REGULAR_EVENT', '定期イベント'),
    ]

    community = models.OneToOneField(
        Community,
        on_delete=models.CASCADE,
        related_name='calendar_entry',
        verbose_name='コミュニティ'
    )
    name = models.CharField('イベント名', max_length=255)
    start_datetime = models.DateTimeField('開始日時')
    end_datetime = models.DateTimeField('終了日時')
    android_support = models.CharField(
        '対応プラットフォーム',
        max_length=20,
        choices=ANDROID_SUPPORT_CHOICES,
        default='PC_ANDROID'
    )
    join_condition = models.TextField('参加条件', blank=True, default='')
    event_detail = models.TextField('イベント詳細', blank=True, default='')
    organizer = models.CharField('主催者', blank=True, max_length=255)
    how_to_join = models.TextField('参加方法', blank=True, default='')
    note = models.TextField('備考', blank=True, null=True)
    is_overseas_user = models.BooleanField('海外ユーザー向け', default=False)
    event_genres = models.JSONField('イベントジャンル', blank=True, default=list)

    def clean(self):
        if self.start_datetime >= self.end_datetime:
            raise ValidationError("開始日時は終了日時よりも前でなければなりません。")

        # イベントジャンルのバリデーション
        valid_genres = set(dict(self.EVENT_GENRE_CHOICES).keys())
        if not set(self.event_genres).issubset(valid_genres):
            raise ValidationError("無効なイベントジャンルが含まれています。")

    def get_event_genres_display(self):
        """イベントジャンルの表示名を返す"""
        genre_dict = dict(self.EVENT_GENRE_CHOICES)
        return [genre_dict.get(genre, genre) for genre in self.event_genres]

    def __str__(self):
        return f"{self.name} ({self.start_datetime.strftime('%Y-%m-%d %H:%M')} - {self.end_datetime.strftime('%H:%M')})"
