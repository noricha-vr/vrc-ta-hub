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
        # GLOBAL: トップ/集会一覧/イベント一覧など特定 community に紐付かないページ。
        # community=NULL, object_id=0 で保存し、superuser 専用のサイト全体集計で参照する
        GLOBAL = 'global', 'サイト全体（紐付けなし）'

    page_path = models.CharField('ページパス', max_length=255, db_index=True)
    date = models.DateField('日付', db_index=True)
    content_type = models.CharField(
        'コンテンツ種別', max_length=20, choices=ContentType.choices
    )
    # 権限判定の基点。COMMUNITY/EVENT_DETAIL では必ず保持、GLOBAL のときは NULL。
    # 集計クエリは必ずこのFKで絞ること（他community混入防止、superuserのみNULL含めて取得可）
    community = models.ForeignKey(
        'community.Community',
        on_delete=models.CASCADE,
        related_name='page_analytics',
        db_index=True,
        verbose_name='集会',
        null=True,
        blank=True,
    )
    # Community.pk または EventDetail.pk。content_type で対象モデルを判別する。
    # GLOBAL の場合は 0（紐付け対象がないことを表す sentinel）
    object_id = models.PositiveIntegerField('オブジェクトID', default=0)
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


class PosterClick(models.Model):
    """集会ポスター画像のクリック数（GA4 カスタムイベント `poster_click` 由来）。

    クライアント側で `gtag('event', 'poster_click', {community_id})` を送信し、
    GA4 Data API から日次で取得して集計する。集会主催者が「自分の集会のサムネが
    どれだけクリックされたか」を確認する用途。

    PageAnalytics と分離した理由:
    - page_view ではない（custom event）ためディメンションが異なる
    - 1件 = (community, date) 単位の素朴な集計で十分（流入元別の保存は不要）
    """

    community = models.ForeignKey(
        'community.Community',
        on_delete=models.CASCADE,
        related_name='poster_clicks',
        db_index=True,
        verbose_name='集会',
    )
    date = models.DateField('日付', db_index=True)
    clicks = models.PositiveIntegerField('クリック数', default=0)
    users = models.PositiveIntegerField('クリックユーザー数', default=0)

    class Meta:
        verbose_name = 'ポスタークリック'
        verbose_name_plural = 'ポスタークリック'
        db_table = 'poster_click'
        unique_together = ('community', 'date')
        ordering = ['-date']

    def __str__(self):
        return f'{self.date} community={self.community_id} clicks={self.clicks}'
