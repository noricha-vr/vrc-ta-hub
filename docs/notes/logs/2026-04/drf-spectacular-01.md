## GatheringListSerializer の schema 生成エラー修正
- 日付: 2026-04-01
- 関連: #150
- 状況: `GatheringListSerializer` を使った `/api/v1/community/gathering-list/` の OpenAPI schema を生成していた
- 問題: `GatheringListSerializer` が `BaseSerializer` だったため、drf-spectacular が `.fields` を読めず `AttributeError` で `/api/schema/` が 500 になった
- 対応: `GatheringListSerializer` を `Serializer` ベースに変更し、`get_fields()` で schema 用フィールド定義を追加した。加えて `/api/schema/?format=json` に `GatheringList` schema が含まれるテストを追加した
- → how/drf-spectacular.md に知識として追記済み
