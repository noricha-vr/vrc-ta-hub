# DRF Spectacular パターン

## `BaseSerializer` を schema の response にそのまま渡さない
- 問題: `drf-spectacular` は serializer schema 生成時に `.fields` を参照するため、`BaseSerializer` を `extend_schema(responses=...)` に渡すと `AttributeError` で `/api/schema/` が 500 になることがある
- 解決: 実レスポンス変換が `to_representation()` 中心でも、schema に出す serializer は `serializers.Serializer` を使い、`get_fields()` などで OpenAPI 用フィールド定義を持たせる
- 教訓: 実レスポンス用ロジックと OpenAPI 定義を別々に持つと乖離しやすいので、可能なら同じ serializer クラス内で両方を管理する
