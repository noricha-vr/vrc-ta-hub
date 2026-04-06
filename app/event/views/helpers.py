import re

from google.auth import default
from google.cloud import bigquery

from event.models import EventDetail

from .compat import get_bigquery_state, set_bigquery_state


def _can_applicant_edit_approved_lt(user, event_detail: EventDetail) -> bool:
    """発表者本人が承認済みLTを編集できるか判定する。"""
    if not getattr(user, "is_authenticated", False):
        return False
    return (
        event_detail.detail_type == "LT"
        and event_detail.status == "approved"
        and event_detail.applicant_id == user.id
    )


def can_manage_event_detail(user, event_detail: EventDetail) -> bool:
    """イベント詳細の更新・記事生成可否を返す。"""
    if not getattr(user, "is_authenticated", False):
        return False
    return (
        getattr(user, "is_superuser", False)
        or event_detail.event.community.can_edit(user)
        or _can_applicant_edit_approved_lt(user, event_detail)
    )


def _get_bigquery_client():
    """BigQueryクライアントを遅延初期化して返す。"""
    client, project = get_bigquery_state()
    if client is None:
        import os

        if os.environ.get("TESTING"):
            from unittest.mock import MagicMock

            client = MagicMock()
            project = "test-project"
        else:
            credentials, project = default()
            client = bigquery.Client(
                credentials=credentials,
                project=project,
                location="asia-northeast1",
            )
        set_bigquery_state(client, project)

    return client, project


def extract_video_id(youtube_url):
    """
    YouTube URLからvideo_idを抽出する関数。

    Note:
        タイムスタンプも取得したい場合は extract_video_info() を使用してください。
    """
    if not youtube_url:
        return None
    pattern = (
        r"(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/(?:[^\/\n\s]+\/\S+\/|"
        r"(?:v|e(?:mbed)?)\/|\S*?[?&]v=)|youtu\.be\/)([a-zA-Z0-9_-]{11})"
    )
    match = re.search(pattern, youtube_url)
    if match:
        return match.group(1)
    return None


def extract_video_info(youtube_url):
    """
    YouTube URLからvideo_idとタイムスタンプ（秒）を抽出する関数。

    Args:
        youtube_url: YouTube URL（例: https://www.youtube.com/watch?v=xxx?t=123）

    Returns:
        tuple: (video_id, start_time)
            - video_id: YouTube動画のID（11文字）またはNone
            - start_time: 開始秒数（整数）またはNone

    Examples:
        - ?t=123 → 123秒
        - &t=123 → 123秒
        - ?t=1m30s → 90秒
        - タイムスタンプなし → None
    """
    if not youtube_url:
        return None, None

    video_id = extract_video_id(youtube_url)

    start_time = None
    time_pattern = r"[?&]t=(\d+(?:m(?:\d+s)?|s)?)"
    time_match = re.search(time_pattern, youtube_url)

    if time_match:
        time_str = time_match.group(1)
        start_time = _parse_youtube_time(time_str)

    return video_id, start_time


def _parse_youtube_time(time_str):
    """
    YouTubeのタイムスタンプ形式を秒に変換する。

    Args:
        time_str: タイムスタンプ文字列（例: "123", "1m30s", "90s"）

    Returns:
        int: 秒数

    Examples:
        - "123" → 123
        - "1m30s" → 90
        - "90s" → 90
        - "2m" → 120
    """
    if time_str.isdigit():
        return int(time_str)

    total_seconds = 0

    minutes_match = re.search(r"(\d+)m", time_str)
    if minutes_match:
        minutes_to_seconds = 60
        total_seconds += int(minutes_match.group(1)) * minutes_to_seconds

    seconds_match = re.search(r"(\d+)s", time_str)
    if seconds_match:
        total_seconds += int(seconds_match.group(1))

    return total_seconds if total_seconds > 0 else None
