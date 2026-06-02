import os
import uuid
from urllib.parse import urlencode

from django.core.validators import RegexValidator
from django.db import models

from website.constants import build_site_url

# utm_source / utm_medium に許す文字種。GA4 / UA 標準の慣習（英数 + - _ .）に揃え、
# 改行や絵文字での QR 量産・改ざんを防ぐ。
# `\A...\Z` を使うのは `^...$` だと re.MULTILINE 不要でも `$` が `\n` 直前にマッチして
# 'flyer\n' のような値が通ってしまうため（Python 正規表現の仕様）
_UTM_TOKEN_VALIDATOR = RegexValidator(
    regex=r'\A[A-Za-z0-9_.\-]+\Z',
    message='半角英数とハイフン・アンダースコア・ドットのみ使用できます。',
)


def qr_image_upload_to(instance, filename):
    """QR画像を `qr_codes/<uuid>.png` に正規化する。

    元ファイル名（推測可能名・連番）を公開URLに出さず、R2上での衝突を避ける。
    拡張子は常に `.png` に強制（クライアントから来たファイル名は信頼しない）。
    """
    ext = os.path.splitext(filename)[1].lower() or '.png'
    if ext != '.png':
        ext = '.png'
    return f'qr_codes/{uuid.uuid4().hex}{ext}'


class PageAnalytics(models.Model):
    """GA4 から取得したページ別アクセスデータの日次蓄積モデル。

    1行 = (page_path, date, source_medium, campaign) の組み合わせごとの集計値。
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
    # GA4 sessionCampaignName。utm_campaign 未指定セッションは '(not set)'。
    # unique_together に含めることで、同一 (path, date, source_medium) でも campaign 違いを別行で保存する。
    campaign = models.CharField(
        'キャンペーン', max_length=128, default='(not set)', db_index=True,
    )

    class Meta:
        verbose_name = 'ページ解析'
        verbose_name_plural = 'ページ解析'
        db_table = 'page_analytics'
        unique_together = ('page_path', 'date', 'source_medium', 'campaign')
        ordering = ['-date', '-pv']

    def __str__(self):
        return f'{self.date} {self.page_path} ({self.source_medium}/{self.campaign})'


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


class Campaign(models.Model):
    """主催者が発行する UTM キャンペーン（QR コード入りチラシ等の流入計測用）。

    主催者・スタッフは自集会の Campaign を作成し、QR 画像つきの URL を取得して
    チラシ・SNS で配布できる。GA4 が utm_* を自動でセッション属性として記録するため、
    PageAnalytics.campaign カラム経由でキャンペーン別 PV をダッシュボードに表示する。

    権限境界:
    - community FK 必須（superuser-only ではなく主催者解放）
    - 全ての CRUD は accessible_community_ids でフィルタする
    """

    community = models.ForeignKey(
        'community.Community',
        on_delete=models.CASCADE,
        related_name='campaigns',
        verbose_name='集会',
    )
    name = models.CharField('表示名', max_length=100, help_text='例: 技術書博 5/10 配布チラシ')
    utm_source = models.CharField(
        'utm_source', max_length=64,
        validators=[_UTM_TOKEN_VALIDATOR],
        help_text='流入元の媒体名。例: flyer / poster / twitter（半角英数記号のみ）',
    )
    utm_medium = models.CharField(
        'utm_medium', max_length=64, default='qr',
        validators=[_UTM_TOKEN_VALIDATOR],
        help_text='流入手段。QR コード経由なら qr のままで OK（半角英数記号のみ）',
    )
    utm_campaign = models.SlugField(
        'utm_campaign', max_length=64,
        help_text='キャンペーン識別子。例: 20260510-gishohaku（半角英数とハイフン）',
    )
    landing_path = models.CharField(
        '着地パス', max_length=255, default='/',
        help_text='例: / または /community/123/',
    )
    qr_image = models.ImageField(
        'QR画像', upload_to=qr_image_upload_to, blank=True,
    )
    distributed_at = models.DateField('配布予定日', null=True, blank=True)
    note = models.TextField('メモ', blank=True)
    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    updated_at = models.DateTimeField('更新日時', auto_now=True)

    class Meta:
        verbose_name = 'キャンペーン'
        verbose_name_plural = 'キャンペーン'
        db_table = 'campaign'
        unique_together = ('community', 'utm_campaign')
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} ({self.utm_campaign})'

    @property
    def url(self) -> str:
        """配布用 URL（landing_path + UTM クエリ）を組み立てる。"""
        query = urlencode({
            'utm_source': self.utm_source,
            'utm_medium': self.utm_medium,
            'utm_campaign': self.utm_campaign,
        })
        path = self.landing_path if self.landing_path.startswith('/') else f'/{self.landing_path}'
        return f'{build_site_url(path)}?{query}'
