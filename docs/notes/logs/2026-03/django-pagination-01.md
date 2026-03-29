## 集会一覧の壊れた page クエリで 500 エラー
- 日付: 2026-03-29
- 関連: #132
- 状況: 集会一覧ページで `page` クエリ付きアクセス時の 500 エラーを修正した
- 問題: `CommunityListView` が `page` を `int()` に直接変換していて、`"1'"` のような不正値で ValueError が発生していた
- 対応:
  1. `Paginator.validate_number()` に置き換えて Django 標準のページ番号検証へ寄せた
  2. 不正値・範囲外ページを 1 ページ目へ戻すよう統一した
  3. 壊れた `page`、範囲外 `page`、`page=last` の回帰テストを追加した
- → how/django-pagination.md に知識として追記済み
