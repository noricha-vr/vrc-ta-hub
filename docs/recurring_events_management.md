# 定期イベント管理システム

## 概要

VRC技術学術ハブでは、定期的に開催されるイベントを効率的に管理するための定期イベント管理システムを提供しています。このシステムにより、毎週・隔週・毎月などの定期開催パターンを設定し、自動的に未来のイベントを生成できます。

## システム構成

### データモデル

#### RecurrenceRule（定期ルール）
定期開催のパターンを定義するモデルです。

- **frequency**: 開催頻度
  - `WEEKLY`: 毎週/隔週
  - `MONTHLY_BY_DATE`: 毎月特定日
  - `MONTHLY_BY_WEEK`: 毎月第N曜日
  - `OTHER`: カスタムルール（LLM使用）
- **interval**: 間隔（例：2 = 隔週）
- **week_of_month**: 第何週か（MONTHLY_BY_WEEKの場合）
- **custom_rule**: カスタムルール説明（OTHERの場合）
- **start_date**: 起点日（隔週計算の基準）
- **end_date**: 終了日（オプション）
- **community**: 紐づくコミュニティ

#### Event（イベント）
実際の開催イベントを表すモデルです。

- **is_recurring_master**: マスターイベントフラグ
- **recurring_master**: 親イベントへの参照（インスタンスの場合）
- **recurrence_rule**: 定期ルールへの参照

### 階層構造

```
RecurrenceRule（定期ルール）
  └─ Master Event（マスターイベント）
       └─ Instance Events（インスタンスイベント）
```

## コマンドラインツール

### generate_recurring_events

定期ルールに基づいて未来のイベントを自動生成するDjangoコマンドです。

#### 基本的な使い方

```bash
# デフォルト（1ヶ月先まで生成）
docker compose exec vrc-ta-hub python manage.py generate_recurring_events

# 3ヶ月先まで生成
docker compose exec vrc-ta-hub python manage.py generate_recurring_events --months=3

# ドライラン（実際には作成せず確認のみ）
docker compose exec vrc-ta-hub python manage.py generate_recurring_events --dry-run

# 未来のイベントをリセットしてから再生成
docker compose exec vrc-ta-hub python manage.py generate_recurring_events --reset-future --months=2
```

#### オプション

| オプション | 説明 | デフォルト |
|----------|------|-----------|
| `--months` | 何ヶ月先まで生成するか | 1 |
| `--dry-run` | 実際には作成せず、作成予定のイベントを表示 | False |
| `--reset-future` | 未来のイベントを削除してから再生成 | False |

#### 動作仕様

1. **重複防止**: 同じ日時のイベントが既に存在する場合はスキップ
2. **期間制限**: RecurrenceRuleのend_dateが設定されている場合はそれに従う
3. **基準日計算**: 最後に生成されたイベントの翌日から生成開始
4. **リセット機能**: 
   - 今日以降の定期イベントインスタンスを削除
   - マスターイベントは削除せず、日付のみ調整

## API エンドポイント

### LLMイベント自動生成エンドポイント

外部から定期的にイベント生成を実行するためのHTTPエンドポイントです。

#### エンドポイント
```
GET /event/generate/
```

#### 認証
HTTPヘッダーに `Request-Token` を含める必要があります。

```bash
curl -X GET \
  -H "Request-Token: YOUR_REQUEST_TOKEN" \
  https://vrc-ta-hub.com/event/generate/?months=2
```

#### パラメータ

| パラメータ | 説明 | デフォルト |
|-----------|------|-----------|
| `months` | 何ヶ月先まで生成するか | 1 |

#### レスポンス

成功時:
```json
{
  "status": "success",
  "message": "LLM event generation completed for 2 months ahead",
  "timestamp": "2025-06-21T12:00:00+09:00"
}
```

エラー時:
```json
{
  "status": "error",
  "message": "エラーメッセージ",
  "timestamp": "2025-06-21T12:00:00+09:00"
}
```

## 定期ルールの種類と例

### 1. 毎週開催（WEEKLY）
```python
RecurrenceRule(
    frequency='WEEKLY',
    interval=1  # 毎週
)
```

### 2. 隔週開催（WEEKLY）
```python
RecurrenceRule(
    frequency='WEEKLY',
    interval=2,  # 隔週
    start_date=date(2025, 6, 1)  # 起点日
)
```

### 3. 毎月特定日（MONTHLY_BY_DATE）
```python
RecurrenceRule(
    frequency='MONTHLY_BY_DATE',
    interval=1  # 毎月
)
```

### 4. 毎月第N曜日（MONTHLY_BY_WEEK）
```python
RecurrenceRule(
    frequency='MONTHLY_BY_WEEK',
    interval=1,  # 毎月
    week_of_month=2  # 第2週
)
```

### 5. カスタムルール（OTHER）
```python
RecurrenceRule(
    frequency='OTHER',
    custom_rule='毎月第2金曜日と第4金曜日'
)
```

カスタムルールはLLM（Gemini）によって解釈され、適切な日付リストが生成されます。

## 運用上の注意事項

### 1. 定期実行の設定

外部のcronジョブやGitHub Actionsなどから定期的にエンドポイントを呼び出すことで、自動的にイベントを生成できます。

例（crontab）:
```bash
# 毎日午前3時に1ヶ月先までのイベントを生成
0 3 * * * curl -X GET -H "Request-Token: ${REQUEST_TOKEN}" https://vrc-ta-hub.com/event/generate/
```

### 2. リセット機能の使用シーン

以下のような場合にリセット機能が有用です：

- 定期ルールを変更した後、既存の未来イベントを再生成したい場合
- イベントの生成ロジックにバグがあり、修正後に再生成したい場合
- テスト環境で生成されたイベントをクリーンアップしたい場合

### 3. パフォーマンスの考慮

- 大量のコミュニティがある場合、生成処理に時間がかかる可能性があります
- `--dry-run` オプションで事前に生成されるイベント数を確認することを推奨

### 4. データ整合性

- RecurrenceRuleには必ずcommunityが紐づいている必要があります
- 孤立したRecurrenceRule（コミュニティなし）は正常に機能しません
- 定期的なクリーンアップスクリプトの実行を推奨

## トラブルシューティング

### イベントが生成されない

1. RecurrenceRuleが正しく設定されているか確認
2. マスターイベントが存在するか確認
3. end_dateが過去の日付になっていないか確認
4. コミュニティのstatusが'approved'になっているか確認

### 重複イベントが生成される

1. 同じ時間帯に手動で作成したイベントがないか確認
2. データベースの整合性を確認（同じ日時のイベントが複数ないか）

### カスタムルールが正しく解釈されない

1. custom_ruleの記述が明確か確認
2. LLMのレスポンスログを確認
3. より具体的な記述に変更（例：「隔週」→「第1・第3金曜日」）

## 関連ファイル

- `/app/event/models.py` - モデル定義
- `/app/event/recurrence_service.py` - 定期ルール処理サービス
- `/app/event/management/commands/generate_recurring_events.py` - 生成コマンド
- `/app/event/views_llm_generate.py` - APIエンドポイント
- `/app/event/tests/test_generate_recurring_events_command.py` - テスト