"""GA4 Data API クライアント。

GA4 プロパティからページ別アクセスデータ（PV・ユーザー数・セッション数）を
日次で取得する。資格情報やトークンはログ・例外に絶対出さない。
"""
import logging
from datetime import date

from django.conf import settings
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
)
from google.oauth2 import service_account

logger = logging.getLogger('analytics')

GA4_SCOPES = ['https://www.googleapis.com/auth/analytics.readonly']

# GA4 が返す date ディメンションは YYYYMMDD の8桁文字列
_GA4_DATE_LENGTH = 8

_DIMENSIONS = ['pagePath', 'date', 'sessionSourceMedium']
_METRICS = ['screenPageViews', 'totalUsers', 'sessions']


def _build_client() -> BetaAnalyticsDataClient:
    """サービスアカウント資格情報で GA4 Data API クライアントを構築する。"""
    credentials = service_account.Credentials.from_service_account_file(
        settings.GOOGLE_APPLICATION_CREDENTIALS,
        scopes=GA4_SCOPES,
    )
    return BetaAnalyticsDataClient(credentials=credentials)


def _parse_ga4_date(value: str) -> str:
    """GA4 の YYYYMMDD 文字列を ISO 形式（YYYY-MM-DD）に変換する。"""
    if len(value) == _GA4_DATE_LENGTH:
        return f'{value[:4]}-{value[4:6]}-{value[6:]}'
    return value


def fetch_page_report(property_id: str, target_date: date) -> list[dict]:
    """指定日のページ別アクセスレポートを GA4 から取得する。

    Args:
        property_id: GA4 Data API 用の数値プロパティID。
        target_date: 取得対象日（start/end とも同日を指定）。

    Returns:
        各行を表す dict のリスト。キーは page_path / date / source_medium /
        pv / users / sessions。
    """
    client = _build_client()
    date_str = target_date.isoformat()

    request = RunReportRequest(
        property=f'properties/{property_id}',
        dimensions=[Dimension(name=name) for name in _DIMENSIONS],
        metrics=[Metric(name=name) for name in _METRICS],
        date_ranges=[DateRange(start_date=date_str, end_date=date_str)],
    )

    response = client.run_report(request)

    # GA4 Data API のデフォルト limit は 10,000 行。サイレント欠落を Fail Loud で検知する。
    # 現規模（14日870行）では発生しないが、成長して上限に達したら警告ログから気付けるようにする。
    if response.row_count > len(response.rows):
        logger.warning(
            'GA4 response truncated: row_count=%d returned=%d property=%s date=%s',
            response.row_count, len(response.rows), property_id, date_str,
        )

    results = []
    for row in response.rows:
        page_path = row.dimension_values[0].value
        row_date = _parse_ga4_date(row.dimension_values[1].value)
        source_medium = row.dimension_values[2].value
        results.append({
            'page_path': page_path,
            'date': row_date,
            'source_medium': source_medium,
            'pv': int(row.metric_values[0].value),
            'users': int(row.metric_values[1].value),
            'sessions': int(row.metric_values[2].value),
        })

    logger.info('GA4 fetch_page_report: property=%s date=%s rows=%d',
                property_id, date_str, len(results))
    return results
