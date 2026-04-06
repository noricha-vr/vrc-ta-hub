# Django views package 化パターン

## package 化しつつ import / patch 互換を保つ
- 問題: 単一の `views.py` を `views/` package に分割すると、既存の `community.views` import と `patch("community.views.requests.post")` のような patch target が壊れやすい
- 解決:
  - `views/__init__.py` で既存の View と補助シンボルを再公開する
  - `requests` / `logger` / 外部 cleanup 関数のような patch 対象は package 側に置き、submodule では `import community.views as views_pkg` 経由で参照する
  - `test_view_exports.py` のような export 回帰テストを追加して package API を固定する
- 教訓: Django の view 分割は import 互換だけでなく patch target 互換まで含めて設計する
