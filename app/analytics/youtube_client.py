"""YouTube Data API の公開統計と Analytics API の同意済み視聴実績を取得する。"""
import logging
import re

from django.conf import settings
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger('analytics')

VIDEO_BATCH_SIZE = 50
# Hub チャンネルはアップロード時に説明欄へ発表ページの URL を書く。説明欄はチャンネル所有者しか
# 編集できないので、発表者が編集できる youtube_url より信頼できる対応として使う。
DETAIL_LINK_RE = re.compile(r'https://vrc-ta-hub\.com/event/detail/(\d+)/')


def fetch_video_statistics(video_ids) -> dict:
    """動画 ID を 50 件ずつ取得し、非公開の統計値は None で返す。"""
    if not settings.GOOGLE_API_KEY:
        logger.warning('YouTube statistics skipped: GOOGLE_API_KEY is not configured')
        return {}

    video_ids = list(dict.fromkeys(video_ids))
    if not video_ids:
        return {}

    youtube = build('youtube', 'v3', developerKey=settings.GOOGLE_API_KEY)
    results = {}
    for offset in range(0, len(video_ids), VIDEO_BATCH_SIZE):
        try:
            response = youtube.videos().list(
                part='statistics',
                id=','.join(video_ids[offset:offset + VIDEO_BATCH_SIZE]),
            ).execute()
        except HttpError as exc:
            # HttpError の URI には API キーが含まれるため、同期側の例外ログには渡さない。
            raise RuntimeError(f'YouTube statistics request failed: HTTP {exc.resp.status}') from None
        for item in response.get('items', []):
            statistics = item.get('statistics', {})
            results[item['id']] = {
                field: int(statistics[api_field]) if statistics.get(api_field) is not None else None
                for field, api_field in (
                    ('view_count', 'viewCount'),
                    ('like_count', 'likeCount'),
                    ('comment_count', 'commentCount'),
                )
            }
    return results


def fetch_video_detail_ids(video_ids) -> dict:
    """動画の説明欄にある発表ページの URL から、動画 ID → 発表 ID を返す（リンクの無い動画は含めない）。"""
    if not settings.GOOGLE_API_KEY:
        logger.warning('YouTube detail links skipped: GOOGLE_API_KEY is not configured')
        return {}

    video_ids = list(dict.fromkeys(video_ids))
    if not video_ids:
        return {}

    youtube = build('youtube', 'v3', developerKey=settings.GOOGLE_API_KEY)
    results = {}
    for offset in range(0, len(video_ids), VIDEO_BATCH_SIZE):
        try:
            response = youtube.videos().list(
                part='snippet',
                id=','.join(video_ids[offset:offset + VIDEO_BATCH_SIZE]),
            ).execute()
        except HttpError as exc:
            # HttpError の URI には API キーが含まれるため、同期側の例外ログには渡さない。
            raise RuntimeError(f'YouTube snippet request failed: HTTP {exc.resp.status}') from None
        for item in response.get('items', []):
            match = DETAIL_LINK_RE.search(item.get('snippet', {}).get('description', ''))
            if match:
                results[item['id']] = int(match.group(1))
    return results


def youtube_analytics_configured() -> bool:
    return all((settings.YOUTUBE_CLIENT_ID, settings.YOUTUBE_CLIENT_SECRET,
                settings.YOUTUBE_ANALYTICS_REFRESH_TOKEN))


def fetch_video_analytics(target_date) -> dict:
    """同意済みチャンネルの指定日を動画別に取得する。資格情報はログに出さない。"""
    if not youtube_analytics_configured():
        logger.warning('YouTube Analytics skipped: OAuth settings are not configured')
        return {}

    try:
        credentials = Credentials(
            None,
            refresh_token=settings.YOUTUBE_ANALYTICS_REFRESH_TOKEN,
            client_id=settings.YOUTUBE_CLIENT_ID,
            client_secret=settings.YOUTUBE_CLIENT_SECRET,
            token_uri='https://oauth2.googleapis.com/token',
        )
        youtube = build('youtubeAnalytics', 'v2', credentials=credentials, cache_discovery=False)
        results = {}
        start_index = 1
        while True:
            response = youtube.reports().query(
                ids='channel==MINE',
                startDate=target_date.isoformat(),
                endDate=target_date.isoformat(),
                metrics='views,estimatedMinutesWatched,averageViewDuration',
                dimensions='video',
                sort='-views',
                maxResults=200,
                startIndex=start_index,
            ).execute()
            rows = response.get('rows', [])
            for video_id, views, minutes, seconds in rows:
                results[video_id] = {
                    'views': int(views),
                    'estimated_minutes_watched': float(minutes),
                    'average_view_duration_seconds': float(seconds),
                }
            if len(rows) < 200:
                return results
            start_index += 200
    except HttpError as exc:
        raise RuntimeError(f'YouTube Analytics request failed: HTTP {exc.resp.status}') from None
    except Exception:
        # OAuth の更新失敗やクライアント構築時の例外にも秘密が含まれうる。
        raise RuntimeError('YouTube Analytics request failed') from None
