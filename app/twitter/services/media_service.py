"""X API メディアアップロード操作を view から分離するサービス."""

from twitter.x_api import upload_media


def upload_media_to_x(image_url: str) -> str | None:
    """画像URLをX APIへアップロードする."""
    return upload_media(image_url)
