"""ガイド画像の長期キャッシュとアプリ JS/CSS のハッシュ付き R2 配信を担う静的ファイルストレージ。"""
from functools import lru_cache
import hashlib
from pathlib import Path

from django.conf import settings
from storages.backends.s3boto3 import S3Boto3Storage

_APP_STATIC_SUFFIXES = ('.js', '.css', '.mjs', '.map')
_APP_STATIC_PREFIXES = (
    'announcement/',
    'analytics/',
    'api_v1/',
    'community/',
    'event/',
    'event_calendar/',
    'guide/',
    'news/',
    'sitemap/',
    'ta_hub/',
    'twitter/',
    'user_account/',
    'vket/',
)
VERSIONED_ASSET_PREFIX = '_assets'


def is_versioned_app_asset(name: str) -> bool:
    """R2 のコンテンツハッシュ付きキーで配信するアプリ同梱 JS/CSS 資産か判定する。"""
    normalized = str(name).lstrip('/')
    return normalized.startswith(_APP_STATIC_PREFIXES) and normalized.endswith(_APP_STATIC_SUFFIXES)


@lru_cache(maxsize=512)
def _static_file_version(path_str: str) -> str:
    """静的ファイルの SHA-256 先頭 10 文字をキャッシュして返す。"""
    target = Path(path_str)
    if not target.is_file():
        return ''
    return hashlib.sha256(target.read_bytes()).hexdigest()[:10]


def versioned_asset_key(name: str, static_root: str | Path | None = None) -> str | None:
    """collectstatic 済み静的ファイルが存在する場合、ハッシュ付き R2 キーを返す。"""
    normalized = str(name).lstrip('/')
    if not is_versioned_app_asset(normalized):
        return None
    root = static_root if static_root is not None else getattr(settings, 'STATIC_ROOT', None)
    if not root:
        return None
    candidate = Path(root) / normalized
    version = _static_file_version(str(candidate))
    if not version:
        return None
    return f'{VERSIONED_ASSET_PREFIX}/{version}/{normalized}'


class GuideStaticFilesStorage(S3Boto3Storage):
    """ガイド画像は従来 R2 長期キャッシュ、アプリ JS/CSS はハッシュ付き R2 キーで配信する。"""

    def get_object_parameters(self, name):
        parameters = super().get_object_parameters(name).copy()
        # 本文のスクリーンショット（.webp）は URL に中身のハッシュが付くので長期でよい。
        # 配布用ポスター（posters/*.jpg）は固定 URL で直接リンクされるため対象外。
        if name.startswith('guide/images/') and name.endswith('.webp'):
            parameters['CacheControl'] = 'public, max-age=31536000, immutable'
        return parameters

    def url(self, name, parameters=None, expire=None, http_method=None):
        key = versioned_asset_key(name)
        if key:
            return super().url(key, parameters=parameters, expire=expire, http_method=http_method)
        return super().url(name, parameters=parameters, expire=expire, http_method=http_method)
