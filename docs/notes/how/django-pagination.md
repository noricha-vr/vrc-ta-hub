# Djangoページネーション入力検証パターン

## `page` クエリの不正値を安全に扱う
- 問題: 一覧ビューで `request.GET["page"]` を `int()` に直接通すと、`"1'"` のような壊れた値で `ValueError` になって 500 エラー化する
- 解決: Django `Paginator.validate_number()` を使って `page` を検証し、`InvalidPage` を捕まえて 1 ページ目へ戻す
- 教訓: `page=last` を含む Django 標準仕様があるので、自前の数値変換より paginator の検証ロジックを優先する
