# Community List Query

## 問題

`CommunityListView` で `Min(events__date)` の集約アノテーションを付けた queryset に対して `count()` すると、MySQL では `GROUP BY` を含む count SQL になりやすい。

この経路は Django が MySQL の GROUP BY 機能情報を参照するため、DB 接続が不安定なタイミングで一覧ページの 500 に直結しやすい。

## 解決

- 検索条件を適用した Community queryset と、表示順用の `latest_event_date` 付与を分ける。
- `search_count` は検索条件適用済み queryset で数える。
- `latest_event_date` は `Subquery` で未来イベントの最短日付を取得し、一覧の並び順だけに使う。
- 回帰テストでは `count()` SQL に `GROUP BY` が含まれないことを確認する。

## 教訓

一覧ページの count に不要な集約・join を載せない。表示用の annotation と件数計算は分離する。
