# 関数一覧

VRC技術学術ハブで使用される主要な関数の一覧です。

## イベント管理関連

### RecurrenceRule モデル

#### `is_occurrence_date(self, check_date)`
- **場所**: `app/event/models.py:48`
- **説明**: 指定された日付がこのルールに従う開催日かどうかを判定
- **パラメータ**: 
  - `check_date`: 判定対象の日付
- **戻り値**: bool - 開催日の場合True

#### `get_next_occurrence(self, after_date)`
- **場所**: `app/event/models.py:71`
- **説明**: 指定日以降の次回開催日を取得
- **パラメータ**: 
  - `after_date`: 基準日
- **戻り値**: date or None - 次回開催日

### RecurrenceService クラス

#### `generate_dates(self, rule, base_date, base_time, months=3)`
- **場所**: `app/event/recurrence_service.py:23`
- **説明**: 定期ルールに基づいて日付リストを生成
- **パラメータ**:
  - `rule`: RecurrenceRuleオブジェクト
  - `base_date`: 基準日
  - `base_time`: 基準時刻
  - `months`: 生成する月数（デフォルト3）
- **戻り値**: List[date] - 生成された日付のリスト

#### `create_recurring_events(self, community, rule, base_date, start_time, duration, months=3)`
- **場所**: `app/event/recurrence_service.py:186`
- **説明**: 定期イベントのインスタンスを作成
- **パラメータ**:
  - `community`: Communityオブジェクト
  - `rule`: RecurrenceRuleオブジェクト
  - `base_date`: 基準日
  - `start_time`: 開始時刻
  - `duration`: 開催時間（分）
  - `months`: 生成する月数
- **戻り値**: List[Event] - 作成されたイベントのリスト

### Googleカレンダー同期

#### `sync_database_to_google(self)`
- **場所**: `app/event/sync_to_google.py`
- **説明**: データベースからGoogleカレンダーへイベントを同期
- **戻り値**: dict - 同期結果（作成数、更新数、削除数）

#### `create_event(self, summary, start_time, end_time, description, recurrence=None)`
- **場所**: `app/event/sync_to_google.py`
- **説明**: Googleカレンダーにイベントを作成
- **パラメータ**:
  - `summary`: イベントタイトル
  - `start_time`: 開始時刻
  - `end_time`: 終了時刻
  - `description`: 説明文
  - `recurrence`: 繰り返しルール（未使用）
- **戻り値**: dict - 作成されたイベント情報

## AI自動生成関連

### コンテンツ生成

#### `generate_blog_from_youtube(event_detail_id)`
- **場所**: `app/event/tasks.py`
- **説明**: YouTube動画からブログ記事を自動生成
- **パラメータ**: 
  - `event_detail_id`: EventDetailのID
- **戻り値**: bool - 成功/失敗

#### `generate_event_summary(event_id)`
- **場所**: `app/event/tasks.py`
- **説明**: イベントの要約を生成
- **パラメータ**: 
  - `event_id`: EventのID
- **戻り値**: str - 生成された要約

## コミュニティ管理

### Community モデル

#### `get_next_event_date(self)`
- **場所**: `app/community/models.py`
- **説明**: 次回開催予定日を取得
- **戻り値**: date or None - 次回開催日

#### `is_active(self)`
- **場所**: `app/community/models.py`
- **説明**: コミュニティがアクティブかどうかを判定
- **戻り値**: bool - アクティブな場合True

## ユーティリティ関数

### 日付処理

#### `generate_custom_dates(rule_text, base_date, months=3)`
- **場所**: `scripts/generate_custom_events.py:22`
- **説明**: カスタムルールに基づいて日付を生成
- **パラメータ**:
  - `rule_text`: カスタムルールのテキスト
  - `base_date`: 基準日
  - `months`: 生成する月数
- **戻り値**: List[date] - 生成された日付のリスト

### データエクスポート

#### `export_active_communities()`
- **場所**: `scripts/export_active_communities.py:19`
- **説明**: アクティブな集会の情報をCSVファイルにエクスポート
- **戻り値**: str - 作成されたCSVファイルのパス

## 管理コマンド

### generate_recurring_events
- **場所**: `app/event/management/commands/generate_recurring_events.py`
- **説明**: 定期イベントのインスタンスを生成（3ヶ月先まで）
- **使用方法**: `python manage.py generate_recurring_events [--months N]`

### sync_to_google
- **場所**: `app/event/management/commands/sync_to_google.py`
- **説明**: データベースのイベントをGoogleカレンダーに同期
- **使用方法**: `python manage.py sync_to_google`