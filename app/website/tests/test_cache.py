"""キャッシュ戦略のテスト

caching.py で定義した CACHES 設定が以下を満たすことを検証する:

1. テスト用 LocMemCache で set/get が round-trip できる
2. KEY_PREFIX (vrc-ta-hub) がキーに付与される
3. 本番用 DatabaseCache の共有テーブルが利用できる
4. TIMEOUT で値が expire する
"""

import os
import subprocess
import sys
import time

from django.conf import settings
from django.core.cache import cache, caches
from django.core.cache.backends.db import DatabaseCache
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings


SETTINGS_BACKEND_PROBE = """
import os
import sys

if os.environ.pop('FORCE_MANAGE_TEST', '') == '1':
    sys.argv = ['manage.py', 'test']

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
from django.conf import settings
print(settings.CACHES['default']['BACKEND'])
"""


class CacheBackendSelectionTest(SimpleTestCase):
    """環境ごとのsettingsロードでdefault cacheを選択する."""

    def _probe_backend(self, **environment: str) -> str:
        probe_environment = os.environ.copy()
        for name in ('K_SERVICE', 'REDIS_URL', 'TESTING', 'FORCE_MANAGE_TEST'):
            probe_environment.pop(name, None)
        probe_environment.update(environment)

        completed = subprocess.run(
            [sys.executable, '-c', SETTINGS_BACKEND_PROBE],
            cwd=settings.BASE_DIR,
            env=probe_environment,
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
        return completed.stdout.strip().splitlines()[-1]

    def test_cloud_run_uses_database_cache(self) -> None:
        backend = self._probe_backend(K_SERVICE='vrc-ta-hub')

        self.assertEqual(backend, 'django.core.cache.backends.db.DatabaseCache')

    def test_testing_environment_overrides_cloud_run(self) -> None:
        backend = self._probe_backend(K_SERVICE='vrc-ta-hub', TESTING='true')

        self.assertEqual(backend, 'django.core.cache.backends.locmem.LocMemCache')

    def test_manage_test_argv_overrides_cloud_run(self) -> None:
        backend = self._probe_backend(K_SERVICE='vrc-ta-hub', FORCE_MANAGE_TEST='1')

        self.assertEqual(backend, 'django.core.cache.backends.locmem.LocMemCache')

    def test_local_without_redis_uses_locmem(self) -> None:
        backend = self._probe_backend()

        self.assertEqual(backend, 'django.core.cache.backends.locmem.LocMemCache')

    def test_local_with_existing_redis_uses_redis_cache(self) -> None:
        backend = self._probe_backend(REDIS_URL='redis://cache.example.invalid:6379/1')

        self.assertEqual(backend, 'django.core.cache.backends.redis.RedisCache')


class CacheRoundTripTest(TestCase):
    """テスト用キャッシュの基本的な set/get 動作."""

    def setUp(self) -> None:
        # 他テストとのキー衝突を避けるためクリアしてから開始
        cache.clear()

    def test_set_get_round_trip(self) -> None:
        """set した値が同一プロセス内で get できる."""
        cache.set('test_key_roundtrip', 'hello-cache', timeout=60)
        self.assertEqual(cache.get('test_key_roundtrip'), 'hello-cache')

    def test_get_returns_none_for_missing_key(self) -> None:
        """未設定のキーは None が返る (Fail Loud ではなく Django 標準仕様)."""
        self.assertIsNone(cache.get('test_key_nonexistent'))

    @override_settings(
        CACHES={
            'default': {
                'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
                'LOCATION': 'login_rate_limit_cache',
                'KEY_PREFIX': 'vrc-ta-hub',
            }
        }
    )
    def test_shared_database_cache_table_is_available(self) -> None:
        """本番と同じDBバックエンドで共有テーブルを利用できる."""
        self.assertIsInstance(caches['default'], DatabaseCache)
        self.assertIn('login_rate_limit_cache', connection.introspection.table_names())
        caches['default'].set('database-cache-roundtrip', 'shared', timeout=60)
        self.assertEqual(caches['default'].get('database-cache-roundtrip'), 'shared')


class CacheKeyPrefixTest(TestCase):
    """KEY_PREFIX が設定値どおり vrc-ta-hub になっている."""

    def test_key_prefix_configured(self) -> None:
        """default キャッシュの KEY_PREFIX が 'vrc-ta-hub' である."""
        backend = caches['default']
        # Django の BaseCache は KEY_PREFIX を self.key_prefix に保持する
        self.assertEqual(backend.key_prefix, 'vrc-ta-hub')

    def test_prefixed_key_is_actually_prefixed(self) -> None:
        """make_key 経由で実キーに vrc-ta-hub プレフィックスが付く."""
        backend = caches['default']
        made = backend.make_key('sample')
        self.assertIn('vrc-ta-hub', made)


class CacheExpirationTest(TestCase):
    """TIMEOUT に達すると値が expire する."""

    @override_settings(
        CACHES={
            'default': {
                'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                'LOCATION': 'vrc-ta-hub-expire-test',
                'TIMEOUT': 1,
                'KEY_PREFIX': 'vrc-ta-hub',
            }
        }
    )
    def test_value_expires_after_timeout(self) -> None:
        """timeout=1 秒で set した値は 2 秒後に消える."""
        from django.core.cache import cache as fresh_cache

        fresh_cache.set('expire_key', 'will-vanish', timeout=1)
        self.assertEqual(fresh_cache.get('expire_key'), 'will-vanish')

        # time.sleep を使う: 1 件だけなので CI でも 2 秒の追加コストで済む
        time.sleep(2)
        self.assertIsNone(fresh_cache.get('expire_key'))
