"""キャッシュ・セッション関連の設定を将来集約する場所.

現状の CACHES は base.py に LocMemCache の最小構成があるのみ。
Redis / Memcached への移行や SESSION_ENGINE のチューニングが必要に
なったらここに集約する。
"""
