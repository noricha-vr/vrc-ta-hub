## Community list の count から GROUP BY を外した
- 日付: 2026-04-20
- 関連: error_reporting app_exception `/community/list/`
- 状況 / 問題 / 対応
  - `/community/list/` の検索一覧で `Min(events__date)` 集約アノテーション付き queryset に `count()` していた。
  - MySQL では count SQL が `GROUP BY` を含む形になり、DB 接続情報参照の経路で 500 が観測された。
  - 検索条件適用済み queryset と表示順用 `latest_event_date` を分離し、`latest_event_date` は `Subquery` で取得する形へ変更した。
- → how/community-list-query.md に知識として追記済み
