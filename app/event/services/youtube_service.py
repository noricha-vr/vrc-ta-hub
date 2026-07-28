"""YouTube字幕取得を担うモジュール."""
from __future__ import annotations

import logging
from typing import Optional

from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptAvailable,
    NoTranscriptFound,
    NotTranslatable,
    TranscriptsDisabled,
    TranslationLanguageNotAvailable,
    VideoUnavailable,
)

from website.settings import GOOGLE_API_KEY

logger = logging.getLogger(__name__)

# 「その動画に字幕が無い」ことを意味する想定内の例外。リトライしても結果は変わらない。
_TRANSCRIPT_ABSENT_ERRORS = (
    TranscriptsDisabled,
    NoTranscriptFound,
    NoTranscriptAvailable,
    NotTranslatable,
    TranslationLanguageNotAvailable,
    VideoUnavailable,
)


def get_transcript(video_id, language='ja') -> Optional[str]:
    """YouTube動画から文字起こしを取得する関数

    Args:
      video_id: YouTube動画のID
      language: 文字起こしの言語のリスト。デフォルトは日本語

    Returns:
      文字起こしテキスト
    """
    if not video_id:
        return ''

    try:
        # APIキーを設定
        youtube = build('youtube', 'v3', developerKey=GOOGLE_API_KEY)

        # 動画の詳細情報を取得
        video_response = youtube.videos().list(
            part='snippet',
            id=video_id
        ).execute()

        if not video_response['items']:
            raise ValueError('動画が見つかりませんでした')

        # 字幕を取得 (認証不要)
        transcript_list = YouTubeTranscriptApi.list_transcripts(
            video_id)

        # 日本語字幕を優先的に取得し、なければ英語字幕を取得して翻訳。
        try:
            transcript = transcript_list.find_transcript(['ja'])
        except NoTranscriptFound:
            logger.exception(
                "日本語字幕が見つからないため英語字幕の翻訳へ"
                "フォールバックします: video_id=%s",
                video_id,
            )
            transcript = transcript_list.find_transcript(
                ['en']).translate('ja')

        # 字幕テキストを結合
        captions_text = "\n".join([entry['text']
                                   for entry in transcript.fetch()])
        return captions_text

    except _TRANSCRIPT_ABSENT_ERRORS as e:
        # 字幕が存在しない・無効化されている等、リトライしても変わらない想定内のケース
        logger.info(f"YouTube字幕が利用できません: video_id={video_id}, reason={type(e).__name__}: {e}")
        return None  # failsafe-ok: 字幕なしでもPDFから生成継続できるため None 続行が正
    except Exception as e:
        # ネットワーク・API 障害等の想定外。字幕なし扱いで続行するが警告として残す
        logger.warning(f"Youtubeから文字起こしを取得するときにエラーが発生しました: {str(e)}")
        return None  # failsafe-ok: 字幕なしでもPDFから生成継続できるため None 続行が正
