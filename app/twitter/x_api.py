"""X (Twitter) API v2 クライアント

OAuth 1.0a User Context で @vrc_ta_hub 公式アカウントにツイートを投稿する。
トークンは環境変数で管理（無期限のため DB 保存・リフレッシュ不要）。
"""

import logging
import os
from urllib.parse import urlparse

import requests
from requests_oauthlib import OAuth1

logger = logging.getLogger(__name__)

X_API_TWEET_URL = "https://api.x.com/2/tweets"
X_MEDIA_UPLOAD_URL = "https://upload.twitter.com/1.1/media/upload.json"
REQUEST_TIMEOUT_SECONDS = 30
MEDIA_UPLOAD_TIMEOUT_SECONDS = 60
MAX_TWEET_LENGTH = 280
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB (X API の上限)
ALLOWED_IMAGE_DOMAINS = frozenset({"data.vrc-ta-hub.com"})
IMAGE_DOWNLOAD_CHUNK_SIZE = 8192


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
    auth = _get_oauth1()
    if not auth:
        return None

    # SSRF防止: 許可ドメインチェック
    parsed = urlparse(image_url)
    if parsed.hostname not in ALLOWED_IMAGE_DOMAINS:
        logger.warning("Blocked image download from untrusted domain: %s", parsed.hostname)
        return None

    try:
        # stream で読み込み、サイズ制限を強制
        image_response = requests.get(image_url, timeout=REQUEST_TIMEOUT_SECONDS, stream=True)
        image_response.raise_for_status()

        chunks = []
        downloaded = 0
        for chunk in image_response.iter_content(chunk_size=IMAGE_DOWNLOAD_CHUNK_SIZE):
            downloaded += len(chunk)
            if downloaded > MAX_IMAGE_SIZE:
                logger.warning("Image exceeded max size: %d bytes", downloaded)
                return None
            chunks.append(chunk)

        image_data = b"".join(chunks)
        content_type = image_response.headers.get('Content-Type', 'image/png')

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
        return None


def post_tweet(text: str, media_ids: list[str] | None = None) -> dict | None:
    """X API v2 でツイートを投稿する（OAuth 1.0a User Context）。

    Args:
        text: 投稿するテキスト (280文字以内)
        media_ids: 添付するメディアIDのリスト (任意)

    Returns:
        成功時: {"id": "...", "text": "..."} の dict
        失敗時: None
    """
    if not text or len(text) > MAX_TWEET_LENGTH:
        logger.error(
            "Tweet text is empty or exceeds %d characters: %d",
            MAX_TWEET_LENGTH,
            len(text) if text else 0,
        )
        return None

    auth = _get_oauth1()
    if not auth:
        return None

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
        data = response.json().get("data", {})
        logger.info("Tweet posted successfully: %s", data.get("id"))
        return data
    except requests.RequestException as e:
        logger.error("Failed to post tweet: %s", e)
        if hasattr(e, "response") and e.response is not None:
            logger.error("Response status: %s", e.response.status_code)
        return None
