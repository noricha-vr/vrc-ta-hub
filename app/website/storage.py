"""ガイド画像の長期キャッシュとコンテナ内 JS/CSS 配信を担う静的ファイルストレージ。"""
from functools import lru_cache
import hashlib
from pathlib import Path

from django.conf import settings
from storages.backends.s3boto3 import S3Boto3Storage

_CONTAINER_STATIC_SUFFIXES = ('.js', '.css', '.mjs', '.map')


@lru_cache(maxsize=512)
def _static_file_version(path_str: str) -> str:
    """静的ファイルの SHA-256 先頭 10 文字をキャッシュして返す。"""
    target = Path(path_str)
    if not target.is_file():
        return ''
    return hashlib.sha256(target.read_bytes()).hexdigest()[:10]


class GuideStaticFilesStorage(S3Boto3Storage):
    """ガイド画像は R2 長期キャッシュ、JS/CSS はコンテナ内の /static/ から配信する。"""

    def get_object_parameters(self, name):
        parameters = super().get_object_parameters(name).copy()
        # 本文のスクリーンショット（.webp）は URL に中身のハッシュが付くので長期でよい。
        # 配布用ポスター（posters/*.jpg）は固定 URL で直接リンクされるため対象外。
        if name.startswith('guide/images/') and name.endswith('.webp'):
            parameters['CacheControl'] = 'public, max-age=31536000, immutable'
        return parameters

    def url(self, name, parameters=None, expire=None, http_method=None):
        normalized = str(name).lstrip('/')
        if normalized.endswith(_CONTAINER_STATIC_SUFFIXES):
            static_root = getattr(settings, 'STATIC_ROOT', None)
            if static_root:
                candidate = Path(static_root) / normalized
                version = _static_file_version(str(candidate))
                if version:
                    return f'/static/{normalized}?v={version}'
        return super().url(name, parameters=parameters, expire=expire, http_method=http_method)
