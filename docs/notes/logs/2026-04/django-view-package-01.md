## event.views を package 化して互換維持した
- 日付: 2026-04-06
- 関連: #198, #206
- 状況: 1743 行の `app/event/views.py` を機能別モジュールへ分割したかった
- 問題: `event.views` を package 化すると既存 import と `patch("event.views.*")` が壊れるリスクがあった
- 対応: `app/event/views/__init__.py` で再 export し、`compat.py` で package 直下の alias を動的参照する構成にした
- → how/django-view-package.md に知識として追記済み
