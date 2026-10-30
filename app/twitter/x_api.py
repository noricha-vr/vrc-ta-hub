"""X (Twitter) API v2 クライアント

OAuth 1.0a User Context で @vrc_ta_hub 公式アカウントにツイートを投稿する。
トークンは環境変数で管理（無期限のため DB 保存・リフレッシュ不要）。
"""

import json
import logging
import os
from typing import NotRequired, TypedDict

import requests
from requests_oauthlib import OAuth1

from twitter.image_download import (
    MAX_IMAGE_SIZE,  # noqa: F401 - 既存の import との互換性
    _is_valid_image_bytes,  # noqa: F401
    download_image,
)
from twitter.tweet_generator import validate_tweet_text

logger = logging.getLogger(__name__)


class PostTweetResult(TypedDict):
    """post_tweet の戻り値。失敗理由を呼び出し側に伝達するため。"""

    ok: bool
    data: dict | None
    status_code: int | None
    error_body: str | None
    retryable: NotRequired[bool]
    outcome_unknown: NotRequired[bool]


def _failure_result(status_code: int | None = None, error_body: str | None = None) -> PostTweetResult:
    return {"ok": False, "data": None, "status_code": status_code, "error_body": error_body}


def _success_result(data: dict) -> PostTweetResult:
    return {"ok": True, "data": data, "status_code": None, "error_body": None}

X_API_TWEET_URL = "https://api.x.com/2/tweets"
X_MEDIA_UPLOAD_URL = "https://upload.twitter.com/1.1/media/upload.json"
REQUEST_TIMEOUT_SECONDS = 30
MEDIA_UPLOAD_TIMEOUT_SECONDS = 60
# ログに出す X API エラー body の上限（UTF-8 バイト数）。全量（1000B）出力はトークン・
# ユーザー入力を含みうるうえログを膨らませるため、原因特定に必要な先頭部分 + errorCode
# だけに絞る。日本語・絵文字を含む body でも上限を超えないよう文字数ではなくバイト数で切る。
ERROR_BODY_LOG_MAX_BYTES = 200


def _extract_error_code(body: str | None) -> str | None:
    """X API のエラー body から機械可読な errorCode を抽出する。

    v2 形式 ``{"title": ..., "type": ...}`` と v1.1 形式
    ``{"errors": [{"code": 187, ...}]}`` の双方に対応する。抽出できなければ None。
    """
    if not body:
        return None
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    errors = data.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        for key in ("code", "type", "title"):
            value = errors[0].get(key)
            if value is not None:
                return str(value)

    for key in ("type", "title", "code"):
        value = data.get(key)
        if value is not None:
            return str(value)
    return None


def _truncate_utf8(text: str, max_bytes: int) -> str:
    """UTF-8 エンコード時に max_bytes を超えないよう文字境界で切り詰める。"""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    # 途中で切れたマルチバイト列は捨てる（errors="ignore"）
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _summarize_error_body(body: str | None) -> str:
    """ログ出力用にエラー body を先頭 200 バイト + errorCode へ縮小する。"""
    snippet = _truncate_utf8(body or "", ERROR_BODY_LOG_MAX_BYTES)
    code = _extract_error_code(body)
    if code:
        return f"errorCode={code} body[:{ERROR_BODY_LOG_MAX_BYTES}B]={snippet}"
    return f"body[:{ERROR_BODY_LOG_MAX_BYTES}B]={snippet}"


def _should_block_x_api_in_tests() -> bool:
    """テスト環境では明示的に許可した場合だけ X API 呼び出しを通す。"""
    from django.conf import settings

    return (
        getattr(settings, 'TESTING', False)
        and os.environ.get("X_API_ALLOW_TEST_CALLS") != "1"
    )


def _get_oauth1() -> OAuth1 | None:
    """OAuth1 認証オブジェクトを生成する共通関数。

    Returns:
        認証情報が揃っている場合は OAuth1 オブジェクト、不足時は None。
    """
    api_key = os.environ.get("X_API_KEY")
    api_secret = os.environ.get("X_API_SECRET")
    access_token = os.environ.get("X_ACCESS_TOKEN")
    access_token_secret = os.environ.get("X_ACCESS_TOKEN_SECRET")

    if not all([api_key, api_secret, access_token, access_token_secret]):
        logger.error("X API credentials are not configured")
        return None

    return OAuth1(
        client_key=api_key,
        client_secret=api_secret,
        resource_owner_key=access_token,
        resource_owner_secret=access_token_secret,
    )


def upload_media(image_url: str) -> str | None:
    """URLから画像をダウンロードしてX APIにアップロードする。

    SSRF防止のためダウンロード先は許可ドメインに限定し、
    画像サイズは MAX_IMAGE_SIZE 以下に制限する。

    Args:
        image_url: アップロードする画像のURL

    Returns:
        成功時: media_id 文字列
        失敗時: None
    """
    if _should_block_x_api_in_tests():
        logger.warning("Blocked X API media upload in test environment")
        return None

    auth = _get_oauth1()
    if not auth:
        return None

    try:
        image = download_image(image_url, log=logger)
        if image is None:
            return None
        image_data, content_type = image

        response = requests.post(
            X_MEDIA_UPLOAD_URL,
            files={'media': ('image', image_data, content_type)},
            auth=auth,
            timeout=MEDIA_UPLOAD_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        media_id = response.json().get('media_id_string')
        logger.info("Media uploaded successfully: %s", media_id)
        return media_id
    except requests.RequestException as e:
        logger.error("Failed to upload media: %s", e)
        if hasattr(e, "response") and e.response is not None:
            logger.error(
                "Response status: %s %s",
                e.response.status_code,
                _summarize_error_body(e.response.text),
            )
        return None


def post_tweet(text: str, media_ids: list[str] | None = None) -> PostTweetResult:
    """X API v2 でツイートを投稿する（OAuth 1.0a User Context）。

    Args:
        text: 投稿するテキスト (X の重み付き280以内)
        media_ids: 添付するメディアIDのリスト (任意)

    Returns:
        PostTweetResult:
            ok=True の場合 data に {"id": "...", "text": "..."} が入る。
            ok=False の場合 status_code と error_body に失敗理由が入る（取得できない場合は None）。
    """
    if _should_block_x_api_in_tests():
        logger.warning("Blocked X API tweet post in test environment")
        return _failure_result(error_body="Blocked in test environment")

    if not text:
        # クライアント側バリデーション失敗 (4xx 相当) は WARNING (docs/logging.md 規約)
        logger.warning("Tweet text is empty")
        return _failure_result(error_body="Tweet text is empty")

    validation_errors = validate_tweet_text(text)
    if validation_errors:
        error_body = f"Tweet text violates local validation: {', '.join(validation_errors)}"
        # クライアント側バリデーション失敗 (4xx 相当) は WARNING (docs/logging.md 規約)
        logger.warning(error_body)
        return _failure_result(error_body=error_body)

    auth = _get_oauth1()
    if not auth:
        return _failure_result(error_body="X API credentials are not configured")

    payload = {"text": text}
    if media_ids:
        payload["media"] = {"media_ids": media_ids}

    try:
        response = requests.post(
            X_API_TWEET_URL,
            json=payload,
            auth=auth,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json().get("data", {}) or {}
        tweet_id = data.get("id")
        if not tweet_id:
            logger.error(
                "Tweet post returned 2xx but response has no tweet id: %s",
                _summarize_error_body(response.text),
            )
            return _failure_result(
                status_code=response.status_code,
                error_body=f"Missing tweet id in response: {response.text[:1000]}",
            )
        logger.info("Tweet posted successfully: %s", tweet_id)
        return _success_result(data)
    except requests.RequestException as e:
        logger.error("Failed to post tweet: %s", e)
        status_code = None
        error_body = str(e)
        if hasattr(e, "response") and e.response is not None:
            status_code = e.response.status_code
            # 通知（Discord）用の error_body は従来どおり 1000B 保持し、ログだけ縮小する。
            error_body = e.response.text[:1000]
            logger.error(
                "Response status: %s %s",
                status_code,
                _summarize_error_body(e.response.text),
            )
        return _failure_result(status_code=status_code, error_body=error_body)
