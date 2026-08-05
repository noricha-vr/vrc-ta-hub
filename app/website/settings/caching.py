"""Django キャッシュ戦略

Cloud Run: DatabaseCache（DB経由でプロセス・インスタンス間共有）
ローカル開発: LocMemCache（既存の Redis があれば RedisCache）

base.py で定義済みの CACHES を環境変数に基づいて上書きする。
settings/__init__.py のロード順 (base → ... → caching) により、
ここでの定義が最終的な CACHES として採用される。

選択ロジック:
- Django test runner → LocMemCache。SimpleTestCase がDRF throttle経由で
  DBアクセスするのを避け、既存のDB禁止テスト契約を維持する
- Cloud Run → DatabaseCache。allauth のレート制限は default
  cache alias を固定利用するため、専用aliasではなくdefaultをDB共有にする。
  テーブルは user_account migration で先に作成してから新revisionへ切り替える
- ローカルで REDIS_URL が設定済み → RedisCache。既存の共有Redisを維持する。
  Django 4.0+ 組み込みの
  django.core.cache.backends.redis.RedisCache を使うので新規パッケージ不要
  (Django 5.2 で動作確認済み)
- 上記以外 → LocMemCache。ローカル開発でキャッシュテーブルを要求しない

KEY_PREFIX を付けることで、共有キャッシュ上のキー衝突を防ぐ。

TIMEOUT は短期キャッシュ前提 (5 分)。長期に保持したい用途では呼び出し側で
個別に timeout 引数を渡す方針。
"""
import sys
from os import environ

from .base import TESTING

# デフォルト TTL (秒)。短期キャッシュ前提
DEFAULT_CACHE_TIMEOUT = 300
# 同一 Redis を別プロジェクトと共有してもキー衝突を防ぐためのプレフィックス
CACHE_KEY_PREFIX = 'vrc-ta-hub'

REDIS_URL = environ.get('REDIS_URL', '').strip()
# K_SERVICE は Cloud Run が全revisionへ自動設定する予約済み環境変数。
# DEBUG の設定漏れに左右されず、DatabaseCache を Cloud Run だけに限定する。
IS_CLOUD_RUN = bool(environ.get('K_SERVICE', '').strip())

# `TESTING=true` はCIや明示実行、`manage.py test` はローカル・composeの
# 通常実行を捕捉する。base.TESTING は環境変数由来の値なので両方をここで併用する。
IS_TEST_RUN = TESTING or 'test' in sys.argv

if IS_TEST_RUN:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'vrc-ta-hub-tests',
            'TIMEOUT': DEFAULT_CACHE_TIMEOUT,
            'KEY_PREFIX': CACHE_KEY_PREFIX,
        }
    }
elif IS_CLOUD_RUN:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
            'LOCATION': 'login_rate_limit_cache',
            'TIMEOUT': DEFAULT_CACHE_TIMEOUT,
            'KEY_PREFIX': CACHE_KEY_PREFIX,
        }
    }
elif REDIS_URL:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': REDIS_URL,
            'TIMEOUT': DEFAULT_CACHE_TIMEOUT,
            'KEY_PREFIX': CACHE_KEY_PREFIX,
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'vrc-ta-hub-local',
            'TIMEOUT': DEFAULT_CACHE_TIMEOUT,
            'KEY_PREFIX': CACHE_KEY_PREFIX,
        }
    }
