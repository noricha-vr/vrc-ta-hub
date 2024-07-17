# app/event_calendar/models.py

from django.core.exceptions import ValidationError
from django.db import models

from community.models import Community
from event.models import Event


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

    event = models.OneToOneField(
        Event,
        on_delete=models.CASCADE,
        related_name='calendar_entry',
        verbose_name='イベント'
    )
    join_condition = models.TextField('参加条件', blank=True, default='')
    event_detail = models.TextField('イベント詳細', blank=True, default='')
    how_to_join = models.TextField('参加方法', blank=True, default='')
    note = models.TextField('備考', blank=True, null=True)
    is_overseas_user = models.BooleanField('海外ユーザー向け', default=False)
    event_genres = models.JSONField('イベントジャンル', blank=True, default=list)

    class Meta:
        db_table = 'calendar_entry'

    def clean(self):
        # イベントジャンルのバリデーション
        valid_genres = set(dict(self.EVENT_GENRE_CHOICES).keys())
        if not set(self.event_genres).issubset(valid_genres):
            raise ValidationError("無効なイベントジャンルが含まれています。")

    def get_event_genres_display(self):
        """イベントジャンルの表示名を返す"""
        genre_dict = dict(self.EVENT_GENRE_CHOICES)
        return [genre_dict.get(genre, genre) for genre in self.event_genres]

    def __str__(self):
        return f"{self.event.community.name} ({self.event.date} {self.event.start_time} - {self.event.end_time})"
