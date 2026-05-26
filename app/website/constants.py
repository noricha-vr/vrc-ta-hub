"""アプリ全体で共有する公開URL・サイズ・TTL設定."""

import os

from website.hosts import normalize_host

DEFAULT_SITE_DOMAIN = "vrc-ta-hub.com"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_HTTP_REFERER = f"https://{DEFAULT_SITE_DOMAIN}/"
OPENROUTER_SITE_TITLE = "VRC TA Hub"


def _normalize_site_url(value: str) -> str:
    stripped = value.strip().rstrip("/")
    if "://" not in stripped:
        return f"https://{stripped}"
    return stripped


def _normalize_root_url(value: str) -> str:
    return f"{_normalize_site_url(value)}/"


SITE_DOMAIN = normalize_host(
    os.environ.get("SITE_DOMAIN")
    or os.environ.get("SITE_URL")
    or os.environ.get("APP_CANONICAL_HOST")
    or DEFAULT_SITE_DOMAIN
)
SITE_URL = _normalize_site_url(os.environ.get("SITE_URL") or f"https://{SITE_DOMAIN}")
OPENROUTER_BASE_URL = os.environ.get(
    "OPENROUTER_BASE_URL",
    DEFAULT_OPENROUTER_BASE_URL,
).rstrip("/")
OPENROUTER_HTTP_REFERER = _normalize_root_url(
    os.environ.get("OPENROUTER_HTTP_REFERER") or DEFAULT_OPENROUTER_HTTP_REFERER
)

CACHE_TTL_HOUR = 60 * 60
MAX_THUMBNAIL_SIZE_BYTES = 10 * 1024 * 1024
MAX_PDF_SIZE_BYTES = 30 * 1024 * 1024

DEFAULT_NEWS_IMAGE_URL = os.environ.get(
    "DEFAULT_NEWS_IMAGE_URL",
    f"https://data.{DEFAULT_SITE_DOMAIN}/images/twitter-negipan-1600.jpeg",
)


def build_site_url(path: str = "") -> str:
    """公開サイトの絶対URLを返す."""
    if not path:
        return SITE_URL
    if path.startswith(("http://", "https://")):
        return path
    if path.startswith("//"):
        return f"https:{path}"
    if path.startswith("/"):
        return f"{SITE_URL}{path}"
    return f"{SITE_URL}/{path}"


def build_openrouter_extra_headers() -> dict[str, str]:
    """OpenRouterへ送るサイト識別ヘッダを返す."""
    return {
        "HTTP-Referer": OPENROUTER_HTTP_REFERER,
        "X-Title": OPENROUTER_SITE_TITLE,
    }


def is_site_domain(hostname: str | None) -> bool:
    """自ドメインまたはそのサブドメインならTrueを返す."""
    if not hostname:
        return False
    normalized_hostname = hostname.lower().rstrip(".")
    normalized_site_domain = SITE_DOMAIN.lower().rstrip(".")
    return (
        normalized_hostname == normalized_site_domain
        or normalized_hostname.endswith(f".{normalized_site_domain}")
    )
