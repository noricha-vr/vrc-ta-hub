# API v1 ドキュメント

このAPIは、コミュニティ、イベント、およびイベント詳細に関する情報を提供します。

## エンドポイント

ベースURL: `/api/v1/`

### コミュニティ

- `GET /community/`: すべてのコミュニティを取得
- `GET /community/{id}/`: 特定のコミュニティを取得
- `GET /community/gathering-list/`: TaAGatheringListSys 向けの `sample.json` 互換形式で集会一覧を取得

#### フィルタリングオプション

- `name`: コミュニティ名（部分一致）
- `weekdays`: 開催曜日

### イベント

- `GET /event/`: すべてのイベントを取得
- `GET /event/{id}/`: 特定のイベントを取得

#### フィルタリングオプション

- `community`: 集会ID（完全一致）。例: `/api/v1/event/?community=42`
- `name`: コミュニティ名（部分一致）
- `weekday`: 開催曜日
- `start_date`: 開始日（以降）
- `end_date`: 終了日（以前）

### イベント詳細

- `GET /event_detail/`: すべてのイベント詳細を取得
- `GET /event_detail/{id}/`: 特定のイベント詳細を取得

#### フィルタリングオプション

- `community`: 集会ID（完全一致）。例: `/api/v1/event_detail/?community=42`
- `theme`: テーマ（部分一致）
- `speaker`: 発表者（部分一致）
- `start_date`: イベント開催日（以降）
- `end_date`: イベント開催日（以前）
- `start_time`: 開始時間

### 撮影の同意

自動撮影ツールが「撮ってよいか」を判断するための項目です。

| 項目 | 出る場所 | 値 |
|------|----------|-----|
| `recording_allowed` | コミュニティ（`/community/`、`/event/` と `/event_detail/` の `event.community` ネストを含む） | `true`（既定）= 撮影を許可 / `false` = この集会は自動撮影の対象外 |
| `recording_policy` | イベント詳細（`/event_detail/`、`/event-details/`） | `public`（既定）= 撮影して YouTube で公開 / `allowed` = 撮影するが公開しない / `forbidden` = 撮影しない |

イベント詳細の読み取りには `detail_type`（`LT` = 発表 / `SPECIAL` = 特別企画 / `BLOG` = ブログ）も出ます。自動撮影の対象を発表に絞る時に使います。

`recording_policy` は API キー認証の `POST` / `PUT` / `PATCH /event-details/` でも指定できます。上の 3 つ以外の値は 400 になります。

## レスポンス形式

すべてのエンドポイントはJSONフォーマットでデータを返します。

## 認証

このAPIは現在、認証を必要としません。

## レート制限

匿名ユーザーと認証済みユーザーに対して、レート制限が適用されています。

## CORS

Cross-Origin Resource Sharing (CORS) が有効になっています。

## 注意事項

- このAPIは読み取り専用です。データの作成、更新、削除はサポートしていません。
- イベントとイベント詳細のエンドポイントは、現在日付以降のデータのみを返します。
- すべてのエンドポイントで、Django Filter Backendを使用したフィルタリングが可能です。

## エラーハンドリング

標準的なHTTPステータスコードを使用してエラーを示します。詳細なエラーメッセージはレスポンスボディに含まれます。
