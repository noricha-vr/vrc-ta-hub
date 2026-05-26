"""X API 投稿操作を view から分離するサービス."""

from twitter.x_api import PostTweetResult
from twitter.x_api import post_tweet


def post_tweet_to_x(text: str, media_ids: list[str] | None = None) -> PostTweetResult:
    """X API 経由でポストを投稿する."""
    return post_tweet(text, media_ids=media_ids)
