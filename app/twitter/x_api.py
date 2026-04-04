"""X (Twitter) API v2 クライアント

OAuth 1.0a User Context で @vrc_ta_hub 公式アカウントにツイートを投稿する。
トークンは環境変数で管理（無期限のため DB 保存・リフレッシュ不要）。
"""

import logging
import os

import requests
from requests_oauthlib import OAuth1

logger = logging.getLogger(__name__)

X_API_TWEET_URL = "https://api.x.com/2/tweets"
REQUEST_TIMEOUT_SECONDS = 30
MAX_TWEET_LENGTH = 280


def post_tweet(text: str) -> dict | None:
    """X API v2 でツイートを投稿する（OAuth 1.0a User Context）。

    Args:
        text: 投稿するテキスト (280文字以内)

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

    api_key = os.environ.get("X_API_KEY")
    api_secret = os.environ.get("X_API_SECRET")
    access_token = os.environ.get("X_ACCESS_TOKEN")
    access_token_secret = os.environ.get("X_ACCESS_TOKEN_SECRET")

    if not all([api_key, api_secret, access_token, access_token_secret]):
        logger.error("X API credentials are not configured")
        return None

    auth = OAuth1(
        client_key=api_key,
        client_secret=api_secret,
        resource_owner_key=access_token,
        resource_owner_secret=access_token_secret,
    )

    try:
        response = requests.post(
            X_API_TWEET_URL,
            json={"text": text},
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
