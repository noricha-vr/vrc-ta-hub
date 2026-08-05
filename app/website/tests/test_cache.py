"""キャッシュ戦略のテスト

caching.py で定義した CACHES 設定が以下を満たすことを検証する:

1. テスト用 LocMemCache で set/get が round-trip できる
2. KEY_PREFIX (vrc-ta-hub) がキーに付与される
3. 本番用 DatabaseCache の共有テーブルが利用できる
4. TIMEOUT で値が expire する
"""

import time

from django.core.cache import cache, caches
from django.core.cache.backends.db import DatabaseCache
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings

from website.settings.caching import (
    build_caches,
    detect_test_run,
    validate_cloud_run_cache_backend,
)


class CacheBackendSelectionTest(SimpleTestCase):
    """環境フラグからdefault cacheを決定的に選択する."""

    def test_test_run_is_detected_from_setting_or_manage_command(self) -> None:
        self.assertTrue(detect_test_run(testing=True, argv=['manage.py']))
        self.assertTrue(
            detect_test_run(testing=False, argv=['manage.py', 'test'])
        )
        self.assertFalse(detect_test_run(testing=False, argv=['manage.py', 'check']))

    def test_cloud_run_uses_database_cache(self) -> None:
        caches_config = build_caches(
            is_cloud_run=True,
            is_testing=False,
            redis_url=None,
        )
        default = caches_config['default']

        self.assertEqual(
            default['BACKEND'],
            'django.core.cache.backends.db.DatabaseCache',
        )
        self.assertEqual(default['OPTIONS']['MAX_ENTRIES'], 100_000)
        self.assertEqual(default['OPTIONS']['CULL_FREQUENCY'], 4)
        self.assertEqual(
            caches_config['healthcheck']['BACKEND'],
            'django.core.cache.backends.locmem.LocMemCache',
        )

    def test_cloud_run_rejects_non_database_default_cache(self) -> None:
        invalid_caches = {
            'default': {
                'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            }
        }

        with self.assertRaises(ImproperlyConfigured):
            validate_cloud_run_cache_backend(
                invalid_caches,
                is_cloud_run=True,
                is_test_run=False,
            )

    def test_testing_environment_overrides_cloud_run(self) -> None:
        caches_config = build_caches(
            is_cloud_run=True,
            is_testing=True,
            redis_url='redis://cache.example.invalid:6379/1',
        )

        self.assertEqual(
            caches_config['default']['BACKEND'],
            'django.core.cache.backends.locmem.LocMemCache',
        )

    def test_local_without_redis_uses_locmem(self) -> None:
        caches_config = build_caches(
            is_cloud_run=False,
            is_testing=False,
            redis_url=None,
        )

        self.assertEqual(
            caches_config['default']['BACKEND'],
            'django.core.cache.backends.locmem.LocMemCache',
        )

    def test_local_with_existing_redis_uses_redis_cache(self) -> None:
        caches_config = build_caches(
            is_cloud_run=False,
            is_testing=False,
            redis_url='redis://cache.example.invalid:6379/1',
        )

        self.assertEqual(
            caches_config['default']['BACKEND'],
            'django.core.cache.backends.redis.RedisCache',
        )


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
