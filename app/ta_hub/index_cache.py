from django.core.cache import cache

from utils.vrchat_time import get_vrchat_today


def get_index_view_cache_key(day=None):
    """トップページのDB由来データキャッシュキーを返す。"""
    if day is None:
        day = get_vrchat_today()
    return f'index_view_data_{day}'


def clear_index_view_cache(day=None):
    """トップページのDB由来データキャッシュを削除する。"""
    cache.delete(get_index_view_cache_key(day))
