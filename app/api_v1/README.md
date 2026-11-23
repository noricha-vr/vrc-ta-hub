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
| `recording_allowed` | コミュニティ（`/community/`、`/event/` と `/event_detail/` の `event.community` ネストを含む） | `true` = ボットによる自動撮影を許可 / `false`（既定）= この集会は自動撮影の対象外 |
| `recording_policy` | イベント詳細（`/event_detail/`、`/event-details/`） | `public` = ボットで撮影して YouTube で公開 / `forbidden` = ボットで撮影しない（`POST` で省略した時の既定） / `allowed` = 旧設定互換のみ（既存レコードに残る場合があり、通常の申請・編集・集会設定および管理画面フォームからは新規選択不可。API の新規登録 `POST /event-details/` のみ互換のため受付） |

イベント詳細の読み取りには `detail_type`（`LT` = 発表 / `SPECIAL` = 特別企画 / `BLOG` = ブログ）も出ます。発表に加え、出演者全員への確認を済ませた特別企画も撮影の対象になりえます。集会の `recording_allowed` と各行の `recording_policy` も確認してください。特別企画の `theme` はタイトル（`h1`）の前後の空白を除き、100 文字に収めた値です。タイトルが空なら `Special Event` です。

`recording_policy` は API キー認証の `POST /event-details/`（新規登録）でだけ指定できます。上の 3 つ以外の値は 400 になります。登録後の `PUT` / `PATCH` で今と違う値を送ると 400 になり、何も保存しません（同じ値の送り直しは通ります）。撮影の扱いは登壇者の同意なので、変えられるのは発表者・主催者の画面だけです。

特別企画の新規登録では、`recording_policy` が `forbidden` 以外なら 400 になります（省略時は `forbidden`）。既存行を特別企画へ種類変更する時も、撮影の扱いが `forbidden` 以外なら 400 になります。撮影する特別企画は、主催者の発表の編集画面で出演者全員への確認にチェックを入れて保存してください。

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
