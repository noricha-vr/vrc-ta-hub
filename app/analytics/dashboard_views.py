"""集会主催者向けアクセス解析ダッシュボード。

機能（MVP + Phase 2）:
- 集会切替（my_list と同じ active_community セッションを共有）
- 期間切替（7 / 30 / 90 日。デフォルト 30）
- 集会全体サマリー（PV/UU/セッション + 前期間比較）
- 記事別アクセス一覧テーブル（PV 順、Phase 2: ソート / CSV エクスポート）
- 流入元別 PV ランキング
- 公開後N日積み上げチャート（上位記事の伸びを比較）
- CSV エクスポート（?format=csv）

権限境界:
- LoginRequiredMixin に加えて accessible_community_ids で community_id を絞る
- 単一集会の community_id でフィルタする際は必ず accessible_community_ids に含まれるか検査
"""
import csv
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.views.generic import TemplateView

from community.models import Community

from . import services

logger = logging.getLogger('analytics')

# CSV Formula Injection（OWASP）防御: Excel/LibreOffice で数式評価される先頭文字
_CSV_DANGEROUS_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


def _csv_safe(value) -> str:
    """CSV セルの値を Formula Injection 対策しつつ文字列化する。

    `=HYPERLINK(...)` 等の埋め込みを `'` でエスケープして無害化する。
    数値・日付は文字列化のみ（先頭が `-` の負数は防御対象だが、本ダッシュボードでは負数を出さない）。
    """
    if value is None:
        return ''
    text = str(value)
    if text and text[0] in _CSV_DANGEROUS_PREFIXES:
        return "'" + text
    return text

# クエリパラメータで受け付ける days の許可リスト（任意値を許すと DoS リスク）
ALLOWED_DAYS = [7, 30, 90]
# 既定日数は services を単一の真実とし、二重管理による不整合を防ぐ
DEFAULT_DAYS = services.DEFAULT_DAYS
# 公開後 N 日積み上げチャートで遡る日数
POST_PUBLISH_DAYS_AFTER = 14
POST_PUBLISH_TOP_N = 5


class AnalyticsDashboardView(LoginRequiredMixin, TemplateView):
    """主催者向けアクセス解析ダッシュボード。"""

    template_name = 'analytics/dashboard.html'

    def _get_days(self) -> int:
        """?days= を許可リストでバリデーションして返す。"""
        raw = self.request.GET.get('days')
        try:
            value = int(raw) if raw else DEFAULT_DAYS
        except (TypeError, ValueError):
            return DEFAULT_DAYS
        return value if value in ALLOWED_DAYS else DEFAULT_DAYS

    def _get_target_community_ids(self, accessible_ids: list[int]) -> list[int]:
        """対象 community_ids を決定する。

        ?community= 指定があり、かつアクセス可能な場合はそれ単体。
        指定なし、または不正な場合はアクセス可能な全 community を返す。
        """
        raw = self.request.GET.get('community')
        if not raw:
            return accessible_ids
        try:
            community_id = int(raw)
        except (TypeError, ValueError):
            return accessible_ids
        if community_id not in accessible_ids:
            # IDOR 防止: 自分が見られない community を指定されたら無視
            return accessible_ids
        return [community_id]

    def get(self, request, *args, **kwargs):
        # CSV エクスポートはテンプレートを返さず直接 HttpResponse
        if request.GET.get('format') == 'csv':
            return self._export_csv()
        return super().get(request, *args, **kwargs)

    def _export_csv(self) -> HttpResponse:
        """記事別アクセス一覧を CSV で返す。Excel で文字化けしないよう UTF-8 BOM 付き。"""
        accessible_ids = services.accessible_community_ids(self.request.user)
        target_ids = self._get_target_community_ids(accessible_ids)
        days = self._get_days()

        rows = services.get_event_detail_breakdown(target_ids, days=days)

        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = (
            f'attachment; filename="analytics-articles-{days}days.csv"'
        )
        # BOM を書き込んで Excel での文字化けを防ぐ。
        # charset=utf-8-sig だと Django が encoding 時に追加 BOM を付け二重 BOM になるため、
        # charset=utf-8 にして自前で1回だけ BOM を書く
        response.write('﻿')
        writer = csv.writer(response)
        writer.writerow([
            'event_detail_id', 'タイトル', '公開日', '集会名',
            'PV', 'ユーザー数', 'セッション数', '主要流入元',
        ])
        for row in rows:
            writer.writerow([
                row['event_detail_id'],
                _csv_safe(row['theme']),
                row['published_at'].isoformat() if row['published_at'] else '',
                _csv_safe(row['community_name']),
                row['pv'],
                row['users'],
                row['sessions'],
                _csv_safe(row['top_source']),
            ])
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        accessible_ids = services.accessible_community_ids(self.request.user)
        days = self._get_days()
        target_ids = self._get_target_community_ids(accessible_ids)

        # 表示用: アクセス可能な community 一覧（切替セレクタで使う）
        accessible_communities = (
            Community.objects
            .filter(id__in=accessible_ids)
            .order_by('name')
        ) if accessible_ids else Community.objects.none()

        # 現在選択中の community（単一の場合のみ）
        selected_community = None
        if len(target_ids) == 1:
            selected_community = next(
                (c for c in accessible_communities if c.id == target_ids[0]),
                None,
            )

        context.update({
            'days': days,
            'allowed_days': ALLOWED_DAYS,
            'accessible_communities': accessible_communities,
            'selected_community': selected_community,
            'has_access': bool(accessible_ids),
            'overall_stats': services.get_overall_stats(target_ids, days=days),
            'daily_series': services.get_daily_series(target_ids, days=days),
            'source_breakdown': services.get_source_breakdown(target_ids, days=days),
            'event_detail_breakdown': services.get_event_detail_breakdown(
                target_ids, days=days,
            ),
            'post_publish_chart': services.get_post_publish_series(
                target_ids,
                days_after=POST_PUBLISH_DAYS_AFTER,
                top_n=POST_PUBLISH_TOP_N,
            ),
            # ポスタークリック集計（権限境界は target_ids ベースで services 側が絞る）
            'poster_clicks': services.get_poster_click_stats(target_ids, days=days),
            # キャンペーン経由アクセス（UTM utm_campaign 別）
            'campaign_breakdown': services.get_campaign_breakdown(target_ids, days=days),
            'campaign_daily': services.get_campaign_daily_series(target_ids, days=days),
        })

        # サイト全体トラフィック（GLOBAL）は superuser のみ取得
        if self.request.user.is_superuser:
            context['global_traffic'] = services.get_global_traffic(days=days)

        return context
