# VRC-TA-Hub 主要関数一覧

このドキュメントは、VRC-TA-Hubプロジェクトの主要な関数をまとめたものです。

## 目次
- [イベント管理 (app/event/)](#イベント管理-appevent)
- [コミュニティ管理 (app/community/)](#コミュニティ管理-appcommunity)
- [API (app/api_v1/)](#api-appapi_v1)
- [カレンダー同期](#カレンダー同期)
- [AI自動生成](#ai自動生成)

## イベント管理 (app/event/)

### views.py

| 関数名 | 説明 | 重要度 |
|--------|------|--------|
| `EventDeleteView` | イベントの削除（Googleカレンダー連携含む） | 高 |
| `EventListView` | イベント一覧表示 | 高 |
| `EventDetailView` | イベント詳細表示 | 高 |
| `sync_calendar_events()` | データベースからGoogleカレンダーへの同期 | 高 |
| `delete_outdated_events()` | 古いイベントの削除処理 | 中 |
| `register_calendar_events()` | Googleカレンダーイベントの登録 | 高 |
| `EventDetailCreateView` | イベント詳細の作成 | 高 |
| `EventDetailUpdateView` | イベント詳細の更新 | 高 |
| `GenerateBlogView` | AI記事生成ビュー | 高 |
| `EventMyList` | ユーザーのイベント一覧 | 高 |
| `EventDetailPastList` | 過去のイベント詳細一覧 | 中 |
| `GoogleCalendarEventCreateView` | Googleカレンダーイベント作成 | 高 |
| `extract_video_id()` | YouTube URLからvideo_idを抽出 | 低 |

### views_recurring.py

| 関数名 | 説明 | 重要度 |
|--------|------|--------|
| `create_recurring_event()` | 定期イベントの作成 | 高 |
| `list_recurring_events()` | 定期イベントの一覧表示 | 中 |

### sync_to_google.py

| 関数名 | 説明 | 重要度 |
|--------|------|--------|
| `DatabaseToGoogleSync.sync_all_communities()` | すべてのコミュニティのイベントを同期 | 高 |
| `DatabaseToGoogleSync.sync_community_events()` | 特定コミュニティのイベントを同期 | 高 |
| `DatabaseToGoogleSync._get_google_events()` | Googleカレンダーからイベントを取得 | 中 |
| `DatabaseToGoogleSync._create_google_event()` | Googleカレンダーにイベントを作成 | 高 |
| `DatabaseToGoogleSync._update_google_event()` | Googleカレンダーのイベントを更新 | 高 |
| `DatabaseToGoogleSync._generate_description()` | イベントの説明文を生成 | 低 |
| `sync_database_to_google()` | 同期処理のエントリーポイント | 高 |

### libs.py

| 関数名 | 説明 | 重要度 |
|--------|------|--------|
| `generate_blog()` | AI記事生成メイン関数 | 高 |
| `get_transcript()` | YouTube動画の文字起こし取得 | 高 |
| `convert_markdown()` | MarkdownをHTMLに変換 | 中 |

### google_calendar.py

| 関数名 | 説明 | 重要度 |
|--------|------|--------|
| `GoogleCalendarService.__init__()` | Googleカレンダーサービスの初期化 | 高 |
| `GoogleCalendarService.create_event()` | カレンダーイベントの作成 | 高 |
| `GoogleCalendarService.update_event()` | カレンダーイベントの更新 | 高 |
| `GoogleCalendarService.delete_event()` | カレンダーイベントの削除 | 高 |
| `GoogleCalendarService.list_events()` | カレンダーイベントの一覧取得 | 高 |
| `GoogleCalendarService._create_weekly_rrule()` | 週次繰り返しルール作成 | 中 |
| `GoogleCalendarService._create_monthly_by_date_rrule()` | 月次日付指定ルール作成 | 中 |
| `GoogleCalendarService._create_monthly_by_week_rrule()` | 月次曜日指定ルール作成 | 中 |

## コミュニティ管理 (app/community/)

### views.py

| 関数名 | 説明 | 重要度 |
|--------|------|--------|
| `CommunityListView` | コミュニティ一覧表示 | 高 |
| `CommunityDetailView` | コミュニティ詳細表示 | 高 |
| `CommunityUpdateView` | コミュニティ情報更新 | 高 |
| `WaitingCommunityListView` | 承認待ちコミュニティ一覧 | 中 |
| `AcceptView` | コミュニティ承認処理 | 高 |
| `RejectView` | コミュニティ非承認処理 | 高 |

### libs.py

| 関数名 | 説明 | 重要度 |
|--------|------|--------|
| `get_join_type()` | 参加方法の種別を判定 | 低 |

## API (app/api_v1/)

### views.py

| 関数名 | 説明 | 重要度 |
|--------|------|--------|
| `CommunityViewSet` | コミュニティAPI (ReadOnly) | 高 |
| `EventViewSet` | イベントAPI (ReadOnly) | 高 |
| `EventDetailViewSet` | イベント詳細API (ReadOnly) | 高 |
| `EventDetailAPIViewSet` | イベント詳細CRUD API | 高 |
| `EventDetailAPIViewSet.my_events()` | 自分のイベント詳細一覧 | 中 |
| `RecurrenceRuleViewSet` | 定期ルールAPI | 高 |
| `RecurrenceRuleViewSet.delete_future_events()` | 未来のイベント削除 | 高 |

## カレンダー同期

### Management Commands

| コマンド | 説明 | 重要度 |
|----------|------|--------|
| `generate_recurring_events` | 定期イベントのインスタンス生成 | 高 |
| `migrate_to_recurring_events` | 既存イベントの定期イベント移行 | 高 |
| `sync_calendar` | カレンダー同期コマンド | 高 |

## AI自動生成

### 主要な処理フロー

1. **記事生成フロー**
   - `GenerateBlogView.post()` → `generate_blog()` → OpenRouter API
   - YouTube動画の文字起こし取得: `get_transcript()`
   - PDFスライドの内容抽出: PyPDFを使用
   - プロンプト生成とAI処理: OpenRouter/Gemini API

2. **コンテンツ変換**
   - Markdown → HTML変換: `convert_markdown()`
   - HTMLサニタイズ: bleachライブラリを使用

## 最近の更新

### Google Calendar同期の修正 (2025年1月)
- `DatabaseToGoogleSync`クラスの実装
- 繰り返しルールを使用しない個別イベント作成方式に変更
- 同期処理の安定性向上

### 定期イベント機能の追加
- `RecurrenceRule`モデルの追加
- `RecurrenceService`による定期イベント生成
- マスター/インスタンス方式による管理

## 環境変数依存

| 変数名 | 使用箇所 | 重要度 |
|--------|----------|--------|
| `GOOGLE_CALENDAR_ID` | カレンダー同期全般 | 高 |
| `GOOGLE_CALENDAR_CREDENTIALS` | Google認証 | 高 |
| `OPENROUTER_API_KEY` | AI記事生成 | 高 |
| `GEMINI_MODEL` | AI使用モデル指定 | 中 |
| `REQUEST_TOKEN` | 同期API認証 | 高 |