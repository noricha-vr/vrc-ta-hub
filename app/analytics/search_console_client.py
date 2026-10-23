"""Search Console の日別検索語の取得と、確定遅延を考慮した一括同期。"""
import logging
import os
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from google.auth import compute_engine
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .models import PageAnalytics, SearchConsoleQuery
from .path_resolver import resolve_page_path

logger = logging.getLogger('analytics')

SEARCH_CONSOLE_SCOPES = ['https://www.googleapis.com/auth/webmasters.readonly']
SEARCH_CONSOLE_LOOKBACK_DAYS = 7
ROW_LIMIT = 25000


def _build_client():
    creds_path = settings.GOOGLE_APPLICATION_CREDENTIALS
    if creds_path and os.path.exists(creds_path):
        credentials = service_account.Credentials.from_service_account_file(
            creds_path, scopes=SEARCH_CONSOLE_SCOPES,
        )
    else:
        credentials = compute_engine.Credentials(scopes=SEARCH_CONSOLE_SCOPES)
    return build('searchconsole', 'v1', credentials=credentials, cache_discovery=False)


def fetch_search_queries(target_date) -> list[dict]:
    """指定日の全ページを取得する。少数の検索語は Google 側で伏せられる。"""
    try:
        client = _build_client()
        results = []
        start_row = 0
        while True:
            response = client.searchanalytics().query(
                siteUrl=settings.SEARCH_CONSOLE_SITE_URL,
                body={
                    'startDate': target_date.isoformat(),
                    'endDate': target_date.isoformat(),
                    'dimensions': ['page', 'query'],
                    'rowLimit': ROW_LIMIT,
                    'startRow': start_row,
                    'dataState': 'all',
                },
            ).execute()
            rows = response.get('rows', [])
            for row in rows:
                results.append({
                    'page_url': row['keys'][0], 'query': row['keys'][1],
                    'clicks': int(row['clicks']), 'impressions': int(row['impressions']),
                    'position': float(row['position']),
                })
            if len(rows) < ROW_LIMIT:
                break
            start_row += ROW_LIMIT
    except HttpError as exc:
        raise RuntimeError(f'Search Console request failed: HTTP {exc.resp.status}') from None
    except Exception:
        # 資格情報の構築失敗・更新失敗にも秘密が含まれうる。
        raise RuntimeError('Search Console request failed') from None
    logger.info('Search Console fetch done: date=%s rows=%d', target_date, len(results))
    return results


def sync_search_console(target_date) -> int:
    """直近7日分を取得後、各日を取得結果で丸ごと置き換える。"""
    try:
        daily_queries = [
            (day, fetch_search_queries(day))
            for day in (
                target_date - timedelta(days=ago)
                for ago in range(SEARCH_CONSOLE_LOOKBACK_DAYS - 1, -1, -1)
            )
        ]
        saved = 0
        skipped = 0
        resolved_paths = {}
        with transaction.atomic():
            for day, rows in daily_queries:
                # URL のクエリ除去や検索語の切り詰めで同一キーになる行は合算する。
                records = {}
                for row in rows:
                    url = urlsplit(row['page_url'])
                    page_path = url.path or '/'
                    if (url.scheme not in ('http', 'https') or url.hostname != 'vrc-ta-hub.com'
                            or len(page_path) > 255):
                        skipped += 1
                        continue
                    if page_path not in resolved_paths:
                        resolved_paths[page_path] = resolve_page_path(page_path) or {
                            'content_type': PageAnalytics.ContentType.GLOBAL,
                            'community': None, 'object_id': 0,
                        }
                    query = row['query'][:255]
                    key = (page_path, query)
                    if key not in records:
                        records[key] = SearchConsoleQuery(
                            date=day, page_path=page_path, query=query,
                            **resolved_paths[page_path],
                        )
                    record = records[key]
                    impressions = record.impressions + row['impressions']
                    record.position = (
                        (record.position * record.impressions + row['position'] * row['impressions'])
                        / impressions if impressions else 0
                    )
                    record.impressions = impressions
                    record.clicks += row['clicks']
                SearchConsoleQuery.objects.filter(date=day).delete()
                SearchConsoleQuery.objects.bulk_create(list(records.values()), batch_size=1000)
                saved += len(records)
    except Exception:
        # DB エラーにも検索語の本文が含まれうるため、呼び出し元へ詳細を渡さない。
        raise RuntimeError('Search Console sync failed') from None
    logger.info(
        'sync_search_console done: since=%s until=%s saved=%d skipped=%d',
        target_date - timedelta(days=SEARCH_CONSOLE_LOOKBACK_DAYS - 1), target_date, saved, skipped,
    )
    return saved
