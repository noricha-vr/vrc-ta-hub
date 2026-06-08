"""ストレージ設定（Cloudflare R2 + ローカルファイル）.

R2/S3 互換のオブジェクトストレージ設定、メディア URL/ROOT、
django-storages の STORAGES 構成を扱う。R2 が無い環境（テスト等）では
ローカルファイルにフォールバックする。
"""
import os

from .base import BASE_DIR, DEBUG

# Cloudflare R2の設定
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
AWS_STORAGE_BUCKET_NAME = os.getenv('AWS_STORAGE_BUCKET_NAME')
AWS_S3_ENDPOINT_URL = os.getenv('AWS_S3_ENDPOINT_URL')
AWS_S3_CUSTOM_DOMAIN = os.getenv('AWS_S3_CUSTOM_DOMAIN')
AWS_S3_URL_PROTOCOL = os.getenv('AWS_S3_URL_PROTOCOL', 'https:')
AWS_S3_SECURE_URLS = AWS_S3_URL_PROTOCOL == 'https:'

# ファイルストレージ（メディア）
if AWS_STORAGE_BUCKET_NAME:
    # R2を使用（本番・開発環境）
    MEDIA_URL = f'{AWS_S3_ENDPOINT_URL}/{AWS_STORAGE_BUCKET_NAME}/'
    AWS_S3_FILE_OVERWRITE = False
    AWS_QUERYSTRING_AUTH = False  # 認証付きのURLを生成しない
    default_storage = {
        'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage',
        'OPTIONS': {
            'access_key': AWS_ACCESS_KEY_ID,
            'secret_key': AWS_SECRET_ACCESS_KEY,
            'bucket_name': AWS_STORAGE_BUCKET_NAME,
            'endpoint_url': AWS_S3_ENDPOINT_URL,
            'custom_domain': AWS_S3_CUSTOM_DOMAIN,
            'file_overwrite': AWS_S3_FILE_OVERWRITE,
            'querystring_auth': AWS_QUERYSTRING_AUTH,
        },
    }
else:
    # ローカルファイルストレージを使用（テスト環境）
    MEDIA_URL = '/media/'
    MEDIA_ROOT = BASE_DIR / 'media'
    default_storage = {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    }

if DEBUG:
    # 静的ファイルはローカル配信（開発時の利便性）
    staticfiles_storage = {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    }
else:
    # 本番は静的ファイルもR2
    staticfiles_storage = {
        'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage',
        'OPTIONS': {
            'access_key': AWS_ACCESS_KEY_ID,
            'secret_key': AWS_SECRET_ACCESS_KEY,
            'bucket_name': AWS_STORAGE_BUCKET_NAME,
            'endpoint_url': AWS_S3_ENDPOINT_URL,
            'custom_domain': AWS_S3_CUSTOM_DOMAIN,
        },
    }

STORAGES = {
    'default': default_storage,
    'staticfiles': staticfiles_storage,
}
