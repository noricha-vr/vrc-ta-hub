# Google Calendar同期の重複問題 - 詳細分析

## 調査結果サマリー

### 1. Google Calendar APIのタイムラグについて

**結論: タイムラグは存在しない**

テスト結果：
- イベント作成直後の取得: **即座に成功**（0秒）
- リスト取得での可視性: **即座に反映**（0秒）
- 更新の反映: **即座に反映**（0秒）
- 削除の反映: **即座に反映**（0秒）

Google Calendar APIは**強い一貫性**を持っており、作成・更新・削除は即座に反映されます。

### 2. 重複が発生する根本原因

現在の`sync_to_google.py`の実装には以下の問題があります：

#### 問題1: 不完全なイベントマッチング

```python
def _get_google_events(self, community: Community, start_date, end_date):
    all_events = self.service.list_events(time_min=..., time_max=...)
    
    # コミュニティ名でフィルタリング（日時の考慮なし）
    community_events = []
    for event in all_events:
        if community.name in event.get('summary', ''):
            community_events.append(event)
    return community_events
```

**問題点**:
- コミュニティ名のみでフィルタリング
- 日時の一致を確認していない
- 同じコミュニティの全イベントが返される

#### 問題2: Google Calendar IDベースの処理フロー

```python
for event in db_events:
    if event.google_calendar_event_id:
        if event.google_calendar_event_id in google_events_dict:
            # 更新
        else:
            # IDをクリアして新規作成
    else:
        # 新規作成
```

**問題点**:
- DBにGoogle Calendar IDがあっても、Googleカレンダーに存在しなければ新規作成
- 同じ日時のイベントが既に存在していても、IDが異なれば新規作成
- これが重複の直接的な原因

### 3. なぜ重複が発生したか

1. **初回同期時**: 正常に作成され、Google Calendar IDがDBに保存される
2. **2回目の同期時**: 
   - `_get_google_events`で取得したイベントリストに含まれない（または別のIDで存在）
   - DBのGoogle Calendar IDと一致しない
   - 新規イベントとして再作成される
   - **結果**: 同じ日時に2つのイベントが存在

## Google Calendar IDベースの管理は有効か？

### 答え: 部分的に有効だが、単独では不十分

**メリット**:
- イベントの一意性が保証される
- 更新・削除が確実に行える
- APIのタイムラグの影響を受けない（そもそもタイムラグがない）

**デメリット**:
- IDの不整合が発生すると重複の原因になる
- 日時ベースのマッチングも併用する必要がある

## 推奨される改善策

### 1. ハイブリッドアプローチ（推奨）

```python
def sync_community_events(self, community, months_ahead=1):
    # DBのイベントを取得
    db_events = Event.objects.filter(...)
    
    # Googleカレンダーのイベントを取得
    google_events = self._get_google_events(community, start_date, end_date)
    
    # 日時でインデックス化
    google_events_by_datetime = {}
    for g_event in google_events:
        dt_key = self._extract_datetime_key(g_event)
        google_events_by_datetime[dt_key] = g_event
    
    for db_event in db_events:
        dt_key = f"{db_event.date}T{db_event.start_time}"
        
        # 1. まず日時でマッチング
        if dt_key in google_events_by_datetime:
            g_event = google_events_by_datetime[dt_key]
            
            # Google Calendar IDを更新（不一致の場合）
            if db_event.google_calendar_event_id != g_event['id']:
                db_event.google_calendar_event_id = g_event['id']
                db_event.save()
            
            # 内容を更新
            self._update_google_event(db_event)
        else:
            # 2. 日時で見つからない場合のみ新規作成
            self._create_google_event(db_event)
```

### 2. 実装上の注意点

1. **イベント作成時**
   - 必ずGoogle Calendar IDをDBに保存
   - エラー時はトランザクションでロールバック

2. **イベント検索時**
   - まず日時でマッチング
   - 見つかった場合はGoogle Calendar IDを同期
   - 見つからない場合のみ新規作成

3. **定期的なメンテナンス**
   - 不整合なGoogle Calendar IDのクリーンアップ
   - 孤立したGoogleカレンダーイベントの削除

### 3. 同期フローの改善

```
1. DBからイベントを取得
2. Googleカレンダーから同期間のイベントを取得
3. 各DBイベントに対して:
   a. 日時 + コミュニティ名でGoogleイベントを検索
   b. 見つかった場合:
      - Google Calendar IDが異なる場合は更新
      - イベント内容を更新
   c. 見つからない場合:
      - 新規作成
      - Google Calendar IDを保存
4. 余分なGoogleイベントを削除（オプション）
```

## 結論

1. **Google Calendar APIにタイムラグはない** - 即座に一貫性のある結果が返される
2. **現在の実装の問題** - コミュニティ名のみでのフィルタリングが重複の原因
3. **Google Calendar IDベースの管理は有効** - ただし日時ベースのマッチングと併用が必要
4. **改善策** - 日時でのマッチングを優先し、Google Calendar IDは補助的に使用