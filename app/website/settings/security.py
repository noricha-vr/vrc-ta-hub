"""セキュリティ関連の Django 設定.

ALLOWED_HOSTS / SECURE_PROXY_SSL_HEADER / SESSION_COOKIE_SECURE /
CSRF_COOKIE_SECURE / SECURE_HSTS_* / CSRF_TRUSTED_ORIGINS /
CORS_ALLOW_ALL_ORIGINS / CORS_URLS_REGEX / DISCORD_AUTH_REQUIRED を扱う。
"""
import os
import sys

from website.hosts import get_canonical_host, normalize_host

from .base import DEBUG, TESTING, _split_csv_env


def _get_canonical_host() -> str:
    return get_canonical_host()


APP_CANONICAL_HOST = _get_canonical_host()


def _build_allowed_hosts() -> list[str]:
    hosts = [
        _get_canonical_host(),
        'localhost',
        '127.0.0.1',
        *_split_csv_env('ALLOWED_HOSTS'),
    ]

    http_host = normalize_host(os.environ.get('HTTP_HOST'))
    if http_host:
        hosts.append(http_host)

    return list(dict.fromkeys(hosts))


ALLOWED_HOSTS = _build_allowed_hosts()

# Cloud Run + nginx プロキシ経由の HTTPS 判定（本番: nginx が https を付加）
# ローカルでは .env.local で HTTP_X_FORWARDED_PROTO=http を設定して is_secure()=False を保証する。
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# 本番環境のセキュリティ強化
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000  # 1年
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

CSRF_TRUSTED_ORIGINS = [
    o for o in [
        'https://vrc-ta-hub.com',
        os.environ.get('CSRF_TRUSTED_ORIGIN'),
    ] if o
]

CORS_ALLOW_ALL_ORIGINS = True
CORS_URLS_REGEX = r'^/api/.*$'

# Discord認証強制ミドルウェアの設定
# DEBUG=True（開発環境）では無効化（ブラウザ操作MCPでのテストのため）
# 本番環境（DEBUG=False）では有効化
# テスト環境では無効化（個別のテストで検証する場合は override_settings で有効化）
DISCORD_AUTH_REQUIRED = not DEBUG

if 'test' in sys.argv or TESTING:
    DISCORD_AUTH_REQUIRED = False
