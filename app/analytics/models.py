from django.db import models


class PageAnalytics(models.Model):
    """GA4 から取得したページ別アクセスデータの日次蓄積モデル。

    1行 = (page_path, date, source_medium) の組み合わせごとの集計値。
    community を権限判定の基点として必ず保持し、集計サービス側でこのFKを
    使ってアクセス可能なデータだけを絞り込む。
    """

    class ContentType(models.TextChoices):
        COMMUNITY = 'community', '集会ページ'
        EVENT_DETAIL = 'event_detail', 'イベント詳細ページ'

    page_path = models.CharField('ページパス', max_length=255, db_index=True)
    date = models.DateField('日付', db_index=True)
    content_type = models.CharField(
        'コンテンツ種別', max_length=20, choices=ContentType.choices
    )
    # 権限判定の基点。集計クエリは必ずこのFKで絞ること（他community混入防止）
    community = models.ForeignKey(
        'community.Community',
        on_delete=models.CASCADE,
        related_name='page_analytics',
        db_index=True,
        verbose_name='集会',
    )
    # Community.pk または EventDetail.pk。content_type で対象モデルを判別する
    object_id = models.PositiveIntegerField('オブジェクトID')
    pv = models.PositiveIntegerField('ページビュー', default=0)
    users = models.PositiveIntegerField('ユーザー数', default=0)
    sessions = models.PositiveIntegerField('セッション数', default=0)
    source_medium = models.CharField('参照元/メディア', max_length=255)

    class Meta:
        verbose_name = 'ページ解析'
        verbose_name_plural = 'ページ解析'
        db_table = 'page_analytics'
        unique_together = ('page_path', 'date', 'source_medium')
        ordering = ['-date', '-pv']

    def __str__(self):
        return f'{self.date} {self.page_path} ({self.source_medium})'
