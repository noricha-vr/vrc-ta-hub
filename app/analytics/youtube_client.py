"""YouTube Data API から動画の公開統計を取得する。"""
import logging

from django.conf import settings
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger('analytics')

VIDEO_BATCH_SIZE = 50


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
