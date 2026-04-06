## community.views package 化で patch 互換を維持
- 日付: 2026-04-06
- 関連: #200, #204
- 状況: `community/views.py` 1224 行を機能別 package に分割したかった
- 問題: `community.views` を import している箇所だけでなく、`patch("community.views.requests.post")` や `patch("community.views.cleanup_community_future_data")` も既存テストで使われていた
- 対応: `views/__init__.py` で公開 API を維持しつつ、patch 対象の依存は package 経由参照に寄せて submodule 分割した
- → how/django-view-package.md に知識として追記済み
