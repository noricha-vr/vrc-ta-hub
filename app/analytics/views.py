"""GA4 アクセス解析の同期 view。

前日分（または指定日）のページ別アクセスデータを GA4 から取得し、
pagePath を内部コンテンツに紐付けて PageAnalytics に冪等に蓄積する。
"""
import logging
import secrets
from datetime import date, datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from community.models import Community

from .ga4_client import fetch_page_report, fetch_poster_click_report
from .models import PageAnalytics, PosterClick
from .path_resolver import resolve_page_path

logger = logging.getLogger('analytics')


def _parse_target_date(request) -> date:
    """GET param 'date' があればパース、無ければ前日を返す。"""
    date_param = request.GET.get('date', '')
    if date_param:
        # 不正な日付は ValueError を送出（Fail Fast。呼び出し側で 400 にする）
        return datetime.strptime(date_param, '%Y-%m-%d').date()
    # Cloud Run は UTC で動くため、TIME_ZONE を考慮した localdate を基準にする
    return timezone.localdate() - timedelta(days=1)


def _is_authorized(request) -> bool:
    """Request-Token を定数時間比較で検証する（fail-closed）。"""
    expected = settings.REQUEST_TOKEN or ''
    provided = request.headers.get('Request-Token', '')
    # トークン未設定（空）時は誰も通さない。設定ミスによる認可バイパスを防ぐ
    if not expected:
        return False
    return secrets.compare_digest(provided, expected)


@require_GET
def sync_analytics(request):
    """GA4 からページ別アクセスデータを取得して蓄積する。"""
    if not _is_authorized(request):
        return HttpResponse('Unauthorized', status=401)

    try:
        target_date = _parse_target_date(request)
    except ValueError:
        return HttpResponse('Invalid date parameter. Use YYYY-MM-DD.', status=400)

    try:
        rows = fetch_page_report(settings.GA4_PROPERTY_ID, target_date)
    except Exception:
        # 資格情報を漏らさないため、例外詳細はレスポンスに含めずログにのみ残す
        logger.error('GA4 fetch failed for date=%s', target_date, exc_info=True)
        return HttpResponse('Failed to fetch GA4 report. Check server logs.', status=500)

    saved = 0
    saved_global = 0
    # 途中失敗時の部分更新を残さないため、保存はまとめて1トランザクションにする
    with transaction.atomic():
        for row in rows:
            # pagePath 解決に失敗した時は utm_campaign 経由で Campaign を逆引きする。
            # landing_path=/ のチラシ QR でも主催者のキャンペーン集計に乗るようにするため
            resolved = resolve_page_path(row['page_path'], row['campaign'])
            if resolved is None:
                # community/event_detail に紐付かない URL は GLOBAL レコードとして保存
                # （superuser のみがサイト全体トラフィックとして閲覧できる）
                PageAnalytics.objects.update_or_create(
                    page_path=row['page_path'],
                    date=row['date'],
                    source_medium=row['source_medium'],
                    campaign=row['campaign'],
                    defaults={
                        'pv': row['pv'],
                        'users': row['users'],
                        'sessions': row['sessions'],
                        'content_type': PageAnalytics.ContentType.GLOBAL,
                        'community': None,
                        'object_id': 0,
                    },
                )
                saved_global += 1
                continue
            PageAnalytics.objects.update_or_create(
                page_path=row['page_path'],
                date=row['date'],
                source_medium=row['source_medium'],
                campaign=row['campaign'],
                defaults={
                    'pv': row['pv'],
                    'users': row['users'],
                    'sessions': row['sessions'],
                    'content_type': resolved['content_type'],
                    'community': resolved['community'],
                    'object_id': resolved['object_id'],
                },
            )
            saved += 1

    # ポスター画像クリック（GA4 カスタムイベント poster_click）の取得・保存。
    # page_view とは別 API 呼び出しのため、失敗しても全体を 500 にせず警告ログのみ。
    saved_poster = 0
    try:
        poster_rows = fetch_poster_click_report(settings.GA4_PROPERTY_ID, target_date)
    except Exception:
        # page_view 同期は成功しており poster_click だけ取得失敗で部分継続するため
        # WARNING に降格 (docs/logging.md 規約: 部分失敗で全体処理が継続する場合)
        logger.warning(
            'GA4 fetch_poster_click_report failed for date=%s', target_date, exc_info=True,
        )
        poster_rows = []

    if poster_rows:
        community_ids = {row['community_id'] for row in poster_rows}
        existing = {
            c.pk: c for c in Community.objects.filter(pk__in=community_ids)
        }
        with transaction.atomic():
            for row in poster_rows:
                community = existing.get(row['community_id'])
                if community is None:
                    # 削除済み community への poster_click は無視（DB に保持しない）
                    continue
                PosterClick.objects.update_or_create(
                    community=community,
                    date=target_date,
                    defaults={'clicks': row['clicks'], 'users': row['users']},
                )
                saved_poster += 1

    logger.info(
        'sync_analytics done: date=%s fetched=%d saved=%d saved_global=%d saved_poster=%d',
        target_date, len(rows), saved, saved_global, saved_poster,
    )
    return HttpResponse(
        f'Analytics synchronized. Date: {target_date}, '
        f'Fetched: {len(rows)}, Saved: {saved}, Global: {saved_global}, Poster: {saved_poster}',
        status=200,
    )
