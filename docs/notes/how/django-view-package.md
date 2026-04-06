# Django View Package パターン

## 既存 import / patch を壊さずに views.py を package 化する
- 問題: 巨大な `views.py` を `views/` package に分割すると、`from event.views import ...` や `patch("event.views.logger")` の既存呼び出しが壊れやすい。
- 解決: `views/__init__.py` で公開 API を再 export し、submodule からは `compat.py` 経由で package 本体の `logger` / `generate_blog` / lazy state を動的参照する。
- 教訓: Django の view 分割では import 互換だけでなく、テストの patch 対象まで含めて互換レイヤを設計する。
