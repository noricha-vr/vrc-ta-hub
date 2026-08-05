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
from collections.abc import Sequence
from os import environ

from django.core.exceptions import ImproperlyConfigured

from .base import TESTING

# デフォルト TTL (秒)。短期キャッシュ前提
DEFAULT_CACHE_TIMEOUT = 300
# 同一 Redis を別プロジェクトと共有してもキー衝突を防ぐためのプレフィックス
CACHE_KEY_PREFIX = 'vrc-ta-hub'
DATABASE_CACHE_BACKEND = 'django.core.cache.backends.db.DatabaseCache'
# 既定の300件では、ランダムemailを使った攻撃で有効なレート制限キーまで
# 頻繁にcullされる。100,000件を上限にし、cull時の削除量も1/4へ抑える。
DATABASE_CACHE_OPTIONS = {
    'MAX_ENTRIES': 100_000,
    'CULL_FREQUENCY': 4,
}
HEALTHCHECK_CACHE = {
    'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    'LOCATION': 'vrc-ta-hub-healthcheck',
    'TIMEOUT': DEFAULT_CACHE_TIMEOUT,
    'KEY_PREFIX': CACHE_KEY_PREFIX,
}


def validate_cloud_run_cache_backend(
    caches_config: dict,
    *,
    is_cloud_run: bool,
    is_test_run: bool,
) -> None:
    """Cloud Runの共有cache契約に違反する設定を起動時に拒否する。"""
    if (
        is_cloud_run
        and not is_test_run
        and caches_config['default']['BACKEND'] != DATABASE_CACHE_BACKEND
    ):
        raise ImproperlyConfigured(
            'Cloud Run requires DatabaseCache for the default cache backend.'
        )


def build_caches(
    *,
    is_cloud_run: bool,
    is_testing: bool,
    redis_url: str | None,
) -> dict:
    """実行環境に対応するDjango cache設定を組み立てる。"""
    normalized_redis_url = (redis_url or '').strip()

    if is_testing:
        default_cache = {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'vrc-ta-hub-tests',
            'TIMEOUT': DEFAULT_CACHE_TIMEOUT,
            'KEY_PREFIX': CACHE_KEY_PREFIX,
        }
    elif is_cloud_run:
        default_cache = {
            'BACKEND': DATABASE_CACHE_BACKEND,
            'LOCATION': 'login_rate_limit_cache',
            'TIMEOUT': DEFAULT_CACHE_TIMEOUT,
            'KEY_PREFIX': CACHE_KEY_PREFIX,
            'OPTIONS': DATABASE_CACHE_OPTIONS.copy(),
        }
    elif normalized_redis_url:
        default_cache = {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': normalized_redis_url,
            'TIMEOUT': DEFAULT_CACHE_TIMEOUT,
            'KEY_PREFIX': CACHE_KEY_PREFIX,
        }
    else:
        default_cache = {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'vrc-ta-hub-local',
            'TIMEOUT': DEFAULT_CACHE_TIMEOUT,
            'KEY_PREFIX': CACHE_KEY_PREFIX,
        }

    return {
        'default': default_cache,
        'healthcheck': HEALTHCHECK_CACHE.copy(),
    }


def detect_test_run(*, testing: bool, argv: Sequence[str]) -> bool:
    """環境変数またはDjango test runnerの引数からテスト実行を判定する。"""
    return testing or 'test' in argv


REDIS_URL = environ.get('REDIS_URL', '').strip()
# K_SERVICE は Cloud Run が全revisionへ自動設定する予約済み環境変数。
# DEBUG の設定漏れに左右されず、DatabaseCache を Cloud Run だけに限定する。
IS_CLOUD_RUN = bool(environ.get('K_SERVICE', '').strip())

# `TESTING=true` はCIや明示実行、`manage.py test` はローカル・composeの
# 通常実行を捕捉する。base.TESTING は環境変数由来の値なので両方をここで併用する。
IS_TEST_RUN = detect_test_run(testing=TESTING, argv=sys.argv)

# test判定とCloud Run判定が同時に真でも、build_caches内でtestを優先する。
CACHES = build_caches(
    is_cloud_run=IS_CLOUD_RUN,
    is_testing=IS_TEST_RUN,
    redis_url=REDIS_URL,
)

# Cloud RunでLocMemへ静かに縮退すると、複数instanceのレート制限が分断される。
# 設定順序の変更や将来の分岐追加で契約が崩れた場合は起動時に失敗させる。
validate_cloud_run_cache_backend(
    CACHES,
    is_cloud_run=IS_CLOUD_RUN,
    is_test_run=IS_TEST_RUN,
)
