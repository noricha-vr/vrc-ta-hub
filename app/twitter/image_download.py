"""X と Misskey で共用する告知画像のダウンロードと検証。"""

import logging
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)
REQUEST_TIMEOUT_SECONDS = 30
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB (X API の上限)
ALLOWED_IMAGE_DOMAINS = frozenset({"data.vrc-ta-hub.com"})
IMAGE_DOWNLOAD_CHUNK_SIZE = 8192

# Content-Type ヘッダー偽装による任意バイナリ送信を防ぐため、
# ダウンロード後にファイル先頭バイトで実画像形式を再検証する。
IMAGE_MAGIC_BYTES = [
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"\xff\xd8\xff",        # JPEG
    b"GIF87a",
    b"GIF89a",
]


def _is_valid_image_bytes(data: bytes) -> bool:
    """ファイル先頭バイトで画像形式を検証 (SSRF/任意バイナリ防止)"""
    if any(data.startswith(magic) for magic in IMAGE_MAGIC_BYTES):
        return True
    # WebP は RIFF....WEBP の特殊形式
    if len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return False


def download_image(image_url: str, *, log=logger) -> tuple[bytes, str] | None:
    """許可ドメインの画像だけをサイズと実体を検証して取得する。

    通信例外は投稿先クライアントで処理する。リダイレクト先への SSRF を防ぎ、
    ストリームはサイズ超過を含む全経路で閉じる。
    """
    parsed = urlparse(image_url)
    if parsed.scheme not in ('https', 'http') or parsed.hostname not in ALLOWED_IMAGE_DOMAINS:
        log.warning("Blocked image download from untrusted domain: %s", parsed.hostname)
        return None

    response = requests.get(
        image_url, timeout=REQUEST_TIMEOUT_SECONDS, stream=True, allow_redirects=False,
    )
    try:
        response.raise_for_status()
        if isinstance(response.status_code, int) and 300 <= response.status_code < 400:
            log.warning("Blocked image download redirect")
            return None

        chunks = []
        downloaded = 0
        for chunk in response.iter_content(chunk_size=IMAGE_DOWNLOAD_CHUNK_SIZE):
            downloaded += len(chunk)
            if downloaded > MAX_IMAGE_SIZE:
                log.warning("Image exceeded max size: %d bytes", downloaded)
                return None
            chunks.append(chunk)

        image_data = b"".join(chunks)
        if not _is_valid_image_bytes(image_data):
            log.warning("Invalid image magic bytes")
            return None
        return image_data, response.headers.get('Content-Type', 'image/png')
    finally:
        response.close()
