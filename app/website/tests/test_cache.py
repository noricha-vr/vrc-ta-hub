"""キャッシュ戦略のテスト

caching.py で定義した CACHES 設定が以下を満たすことを検証する:

1. LocMem バックエンドで set/get が round-trip できる
2. KEY_PREFIX (vrc-ta-hub) がキーに付与される
3. TIMEOUT で値が expire する

REDIS_URL が未設定のテスト環境では LocMemCache が採用される想定。
"""

import time

from django.core.cache import cache, caches
from django.test import TestCase, override_settings


class CacheRoundTripTest(TestCase):
    """LocMem キャッシュの基本的な set/get 動作."""

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
