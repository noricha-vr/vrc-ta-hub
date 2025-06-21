# イベント定期登録とGoogleカレンダー連携システム

## 概要

VRC技術学術ハブでは、定期的に開催されるイベントを効率的に管理するため、RecurrenceRuleモデルを使用した定期イベント登録システムを実装しています。このシステムは、データベースを主体とした管理を行い、Googleカレンダーへの一方向同期を実現しています。

## システムアーキテクチャ

### 1. データベース中心の設計

```
[RecurrenceRule] → [Event（マスター）] → [Event（インスタンス）]
                                      ↓
                              [Google Calendar]
```

- **RecurrenceRule**: 定期開催のルールを定義
- **Event（マスター）**: 定期イベントの親となるイベント
- **Event（インスタンス）**: 実際の開催日ごとのイベント

**重要な仕様**:
- イベントの自動生成期間: **1ヶ月先まで**（サーバー負荷を考慮）
- 同期頻度: 定期的なバッチ処理で実行

### 2. 主要モデル

#### RecurrenceRule

```python
class RecurrenceRule(models.Model):
    frequency = models.CharField(max_length=20, choices=FREQUENCY_CHOICES)
    interval = models.IntegerField(default=1)  # 何週間/月ごとか
    week_of_month = models.IntegerField(null=True, blank=True)  # 第N週
    custom_rule = models.TextField(null=True, blank=True)  # カスタムルール
    start_date = models.DateField(null=True, blank=True)  # 起点日
    end_date = models.DateField(null=True, blank=True)  # 終了日
```

**頻度タイプ（frequency）**:
- `WEEKLY`: 毎週または隔週
- `MONTHLY_BY_DATE`: 毎月（日付指定）
- `MONTHLY_BY_WEEK`: 毎月（第N曜日）
- `OTHER`: カスタムルール（LLM処理）

#### Event

```python
class Event(models.Model):
    community = models.ForeignKey(Community, on_delete=models.CASCADE)
    date = models.DateField('開催日')
    start_time = models.TimeField('開始時刻')
    recurrence_rule = models.ForeignKey(RecurrenceRule, null=True)
    is_recurring_master = models.BooleanField(default=False)
    recurring_master = models.ForeignKey('self', null=True)
    google_calendar_event_id = models.CharField(max_length=255, blank=True)
```

## 定期イベントの仕組み

### 1. 週の計算ロジック

月次イベントで「第N曜日」を指定する場合、その曜日が月内で何回目の出現かを正確に計算します：

```python
def _get_week_of_month(self, date_obj: date) -> int:
    """日付が月の第何週かを取得（その曜日の第何回目の出現か）"""
    # 同じ曜日の第何回目かを計算
    # 例: 2024-11-25（月曜日）は11月の第4月曜日
    count = 0
    for day in range(1, date_obj.day + 1):
        check_date = date_obj.replace(day=day)
        if check_date.weekday() == date_obj.weekday():
            count += 1
    return count
```

### 2. start_dateを使用した開催日判定

隔週開催の管理では、`start_date`（起点日）からの経過週数で開催日を判定します：

```python
def is_occurrence_date(self, check_date):
    """指定された日付がこのルールに従う開催日かどうかを判定"""
    if self.frequency == 'WEEKLY':
        # 曜日が一致しているかチェック
        if check_date.weekday() != self.start_date.weekday():
            return False
        
        # 起点日からの経過週数を計算
        days_diff = (check_date - self.start_date).days
        weeks_diff = days_diff // 7
        
        # intervalごとに開催されるかチェック
        return weeks_diff % self.interval == 0
```

### 3. MONTHLY_BY_WEEK頻度の処理

毎月第N曜日のイベントを生成する際は、明示的に曜日を指定できます：

```python
# RecurrenceRuleでの設定
rule = RecurrenceRule(
    frequency='MONTHLY_BY_WEEK',
    week_of_month=4,  # 第4週
    weekday=0,        # 月曜日（0=月曜、6=日曜）
    start_date=date(2024, 12, 1)
)

# APIでのプレビュー
response = client.post('/api/v1/recurrence-preview/', {
    'frequency': 'MONTHLY_BY_WEEK',
    'week_of_month': 4,
    'weekday': 0,  # 月曜日を明示的に指定
    'base_date': '2024-12-01',
    'base_time': '22:00',
    'months': 3
})
```

### 4. イベント生成フロー

```python
# RecurrenceServiceによる日付生成
service = RecurrenceService()
dates = service.generate_dates(
    rule=recurrence_rule,
    base_date=today,
    base_time=master_event.start_time,
    months=3  # 3ヶ月先まで生成
)

# イベントインスタンスの作成
for date in dates:
    Event.objects.create(
        community=community,
        date=date,
        start_time=master_event.start_time,
        recurring_master=master_event
    )
```

### 5. カスタムルールの処理

確定的なカスタムルールの例：
- `毎月11日`: 毎月11日に開催
- `毎月8のつく日`: 8日、18日、28日に開催
- `最終土曜日`: 毎月の最終土曜日に開催
- `第1・第3土曜日`: 第1週と第3週の土曜日に開催
- `第二・四木曜日`: 第2週と第4週の木曜日に開催

不確定なルール（自動生成しない）：
- `第1土曜日または第2土曜日`
- `2～3週間に1度`
- `月2回ベース、不定期`
- `だいたい隔週`

## Googleカレンダー連携

### 1. 一方向同期の原則

データベース → Googleカレンダーへの一方向同期のみを行います：

```python
# sync_to_google.py
class CalendarSyncService:
    def sync_database_to_google(self):
        """データベースからGoogleカレンダーへの同期"""
        events = Event.objects.filter(
            date__gte=timezone.now().date(),
            community__google_calendar_id__isnull=False
        )
        
        for event in events:
            if not event.google_calendar_event_id:
                # 新規作成
                result = self.service.create_event(...)
                event.google_calendar_event_id = result['id']
                event.save()
```

### 2. 重複防止の仕組み

Googleカレンダーの繰り返しルール（recurrence）は使用せず、個別イベントとして登録：

```python
# 繰り返しルールを無効化
result = self.service.create_event(
    summary=event.community.name,
    start_time=start_datetime,
    end_time=end_datetime,
    description=description,
    recurrence=None  # 繰り返しルールを使用しない
)
```

### 3. 同期コマンド

```bash
# 手動同期（要REQUEST_TOKEN）
curl -X GET -H "Request-Token: YOUR_REQUEST_TOKEN" \
  https://vrc-ta-hub.com/event/update/

# Dockerコンテナから実行
docker compose exec vrc-ta-hub python manage.py sync_to_google
```

## 運用管理

### 1. 定期イベントの作成

```bash
# 1ヶ月先までのイベントを生成
docker compose exec vrc-ta-hub python manage.py generate_recurring_events

# 未来のイベントをリセットして再生成
docker compose exec vrc-ta-hub python manage.py generate_recurring_events --reset-future

# カスタムルールのイベント生成
docker compose exec vrc-ta-hub python scripts/generate_custom_events.py
```

**コマンドオプション**:
- `--reset-future`: 未来のイベントを削除してから再生成（重複防止）

### 2. 開催周期の更新

```bash
# イレギュラーな開催周期を更新
docker compose exec vrc-ta-hub python scripts/update_irregular_schedules_v2.py

# 不確定な開催周期を削除
docker compose exec vrc-ta-hub python scripts/remove_uncertain_recurrence.py
```

### 3. データエクスポート

```bash
# アクティブな集会情報をCSVエクスポート
docker compose exec vrc-ta-hub python scripts/export_active_communities.py
```

## トラブルシューティング

### 1. 重複イベントが発生した場合

```bash
# 重複チェック
docker compose exec vrc-ta-hub python scripts/check_duplicates_only.py

# 重複削除
docker compose exec vrc-ta-hub python scripts/remove_google_duplicates.py
```

### 2. 開催周期がずれた場合

```bash
# 直近の開催日をstart_dateに設定
docker compose exec vrc-ta-hub python scripts/update_start_date_from_recent_events.py

# 未来のイベントを再生成
docker compose exec vrc-ta-hub python scripts/recreate_future_events_final.py
```

### 3. Googleカレンダーとの同期エラー

```bash
# Google Calendar IDのクリア
docker compose exec vrc-ta-hub python scripts/clear_google_calendar_ids.py

# 完全再同期
docker compose exec vrc-ta-hub python scripts/clean_and_resync.py
```

## ベストプラクティス

1. **定期的なイベント生成**: 月1回、3ヶ月先までのイベントを生成
2. **start_dateの管理**: 開催周期が変更された場合は必ずstart_dateを更新
3. **不確定な周期の扱い**: 自動生成せず、手動でイベントを登録
4. **同期の実行**: 単一プロセスで実行し、並列実行を避ける
5. **バックアップ**: 大きな変更前にはデータベースのバックアップを取得
6. **週の計算**: 「第N曜日」は月内でのその曜日の出現回数として計算
7. **MONTHLY_BY_WEEKの使用**: 曜日を明示的に指定して正確な日付生成を保証

## 参考資料

- [Django Recurrence Patterns](https://django-recurrence.readthedocs.io/)
- [Google Calendar API v3](https://developers.google.com/calendar/api/v3/reference)
- [RFC 5545 - iCalendar](https://tools.ietf.org/html/rfc5545)