from typing import List

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

    community = models.OneToOneField(
        Community,
        on_delete=models.CASCADE,
        related_name='calendar_entry',
    )
    join_condition = models.TextField('参加条件', blank=True, default='')
    event_detail = models.TextField('イベント詳細', blank=True, default='')
    how_to_join = models.TextField('参加方法', blank=True, default='')
    note = models.TextField('備考', blank=True, null=True)
    is_overseas_user = models.BooleanField('海外ユーザー向け', default=False)
    event_genres = models.JSONField('イベントジャンル', blank=True, default=list)
    x_post_text = models.TextField('X告知文', blank=True, default='')

    class Meta:
        db_table = 'calendar_entry'

    def clean(self) -> None:
        # イベントジャンルのバリデーション
        valid_genres: List[str] = [choice[0] for choice in self.EVENT_GENRE_CHOICES]
        if not set(self.event_genres).issubset(valid_genres):
            raise ValidationError("無効なイベントジャンルが含まれています。")

    def get_event_genres_display(self) -> List[str]:
        """イベントジャンルの表示名を返す"""
        genre_dict = dict(self.EVENT_GENRE_CHOICES)
        return [genre_dict.get(genre, genre) for genre in self.event_genres]

    @classmethod
    def get_or_create_from_event(cls, event: Event) -> 'CalendarEntry':
        """イベントからCalendarEntryを取得または作成する"""
        calendar_entry, created = cls.objects.get_or_create(
            community=event.community,
            defaults={
                'join_condition': '',
                'event_detail': '',
                'how_to_join': '',
                'note': '',
                'is_overseas_user': False,
                'event_genres': [],
                'x_post_text': ''
            }
        )
        return calendar_entry

    def __str__(self) -> str:
        return f"Calendar Entry for {self.community.name}"
