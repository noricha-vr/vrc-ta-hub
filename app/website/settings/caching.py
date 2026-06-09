"""Django キャッシュ戦略

ローカル開発: LocMemCache (プロセス内、追加依存なし)
本番: REDIS_URL 設定時に RedisCache を自動採用

base.py で定義済みの CACHES を環境変数に基づいて上書きする。
settings/__init__.py のロード順 (base → ... → caching) により、
ここでの定義が最終的な CACHES として採用される。

選択ロジック:
- REDIS_URL が空 (未設定) → LocMemCache。プロセス単体の開発・テスト用途で
  追加依存なしに動く。Cloud Run の単一インスタンス環境でも実害は少ない
- REDIS_URL が設定済み → RedisCache。複数 worker / Cloud Run instance 間で
  状態を共有したい本番向け。Django 4.0+ 組み込みの
  django.core.cache.backends.redis.RedisCache を使うので新規パッケージ不要
  (Django 5.2 で動作確認済み)

KEY_PREFIX を付けることで、同一 Redis を別プロジェクトと共有してもキーが
衝突しない。LocMem でも prefix を揃えておくことで `cache.make_key` の挙動が
本番と一致し、テストでハマるリスクを減らせる。

TIMEOUT は短期キャッシュ前提 (5 分)。長期に保持したい用途では呼び出し側で
個別に timeout 引数を渡す方針。
"""
from os import environ

# デフォルト TTL (秒)。短期キャッシュ前提
DEFAULT_CACHE_TIMEOUT = 300
# 同一 Redis を別プロジェクトと共有してもキー衝突を防ぐためのプレフィックス
CACHE_KEY_PREFIX = 'vrc-ta-hub'

REDIS_URL = environ.get('REDIS_URL', '').strip()

if REDIS_URL:
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
