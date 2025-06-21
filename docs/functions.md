# VRC技術学術ハブ 関数リファレンス

## 目次

### [イベント管理 (event/)](#イベント管理-event)
- [generate_recurring_events](#generate_recurring_events)
- [sync_calendar_events](#sync_calendar_events)
- [generate_llm_events](#generate_llm_events)
- [generate_blog](#generate_blog)

### [コミュニティ管理 (community/)](#コミュニティ管理-community)
- [CommunityUpdateView](#communityupdateview)
- [CalendarEntryUpdateView](#calendarentryupdateview)

### [定期ルール処理 (event/recurrence_service.py)](#定期ルール処理-eventrecurrence_servicepy)
- [RecurrenceService.generate_dates](#recurrenceservicegenerate_dates)
- [RecurrenceRule.is_occurrence_date](#recurrenceruleis_occurrence_date)
- [RecurrenceRule.delete_future_events](#recurrenceruledelete_future_events)

### [Googleカレンダー同期 (event/sync_to_google.py)](#googleカレンダー同期-eventsync_to_googlepy)
- [DatabaseToGoogleSync.sync_all_communities](#databasetogooglesyncync_all_communities)
- [DatabaseToGoogleSync.sync_events](#databasetogooglesyncync_events)

---

## イベント管理 (event/)

### generate_recurring_events
**場所**: `app/event/management/commands/generate_recurring_events.py`

定期ルールに基づいて未来のイベントを生成するDjangoコマンド。

```python
# 使用例
python manage.py generate_recurring_events --months=3 --reset-future
```

**オプション**:
- `--months`: 生成期間（月数）、デフォルト: 1
- `--dry-run`: 実行せずに予定を表示
- `--reset-future`: 未来のイベントを削除してから再生成

### sync_calendar_events
**場所**: `app/event/views.py:285`

データベースからGoogleカレンダーへの同期を実行。

```python
def sync_calendar_events(request):
    """
    重複防止機能付きの同期処理
    HTTPヘッダーのRequest-Tokenで認証
    """
```

### generate_llm_events
**場所**: `app/event/views_llm_generate.py:16`

LLMを使用したイベント自動生成エンドポイント。

```python
@require_http_methods(["GET"])
def generate_llm_events(request):
    """
    generate_recurring_eventsコマンドをHTTP経由で実行
    """
```

### generate_blog
**場所**: `app/event/libs.py`

YouTubeやPDFからブログ記事を自動生成。

```python
def generate_blog(event_detail: EventDetail, model: str = None) -> BlogOutput:
    """
    AI (Gemini) を使用してイベント詳細からブログ記事を生成
    """
```

---

## コミュニティ管理 (community/)

### CommunityUpdateView
**場所**: `app/community/views.py:184`

ログインユーザーのコミュニティ情報を更新。

```python
class CommunityUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    def get_object(self, queryset=None):
        # pkなしでログインユーザーに紐付くコミュニティを取得
        return Community.objects.get(custom_user=self.request.user)
```

### CalendarEntryUpdateView
**場所**: `app/event_calendar/views.py:15`

VRCイベントカレンダーのエントリー情報を更新。

```python
class CalendarEntryUpdateView(LoginRequiredMixin, UpdateView):
    def get_object(self, queryset=None):
        # pkなしでログインユーザーに紐付くコミュニティを取得
        community = Community.objects.get(custom_user=self.request.user)
```

---

## 定期ルール処理 (event/recurrence_service.py)

### RecurrenceService.generate_dates
**場所**: `app/event/recurrence_service.py`

定期ルールに基づいて日付リストを生成。

```python
def generate_dates(self, rule: RecurrenceRule, base_date: date, 
                  base_time: time, months: int, community: Community) -> List[date]:
    """
    頻度タイプに応じた日付生成ロジック
    OTHERの場合はLLMを使用
    """
```

### RecurrenceRule.is_occurrence_date
**場所**: `app/event/models.py`

指定日がルールに合致するか判定。

```python
def is_occurrence_date(self, check_date: date) -> bool:
    """
    定期ルールに基づいて、指定された日付が開催日かどうかを判定
    """
```

### RecurrenceRule.delete_future_events
**場所**: `app/event/models.py`

指定日以降の未来のイベントを削除。

```python
def delete_future_events(self, delete_from_date: date = None) -> int:
    """
    このルールに関連する未来のイベントを削除
    """
```

---

## Googleカレンダー同期 (event/sync_to_google.py)

### DatabaseToGoogleSync.sync_all_communities
**場所**: `app/event/sync_to_google.py`

全コミュニティのイベントを同期。

```python
def sync_all_communities(self, months_ahead: int = 3) -> dict:
    """
    承認済みコミュニティのイベントをGoogleカレンダーに同期
    重複防止機能付き
    """
```

### DatabaseToGoogleSync.sync_events
**場所**: `app/event/sync_to_google.py`

特定コミュニティのイベントを同期。

```python
def sync_events(self, community: Community, start_date: date, 
                end_date: date, stats: dict) -> None:
    """
    コミュニティのイベントをGoogleカレンダーと同期
    日時とコミュニティ名でインデックス化して重複を防ぐ
    """
```

---

## ユーティリティ関数

### create_calendar_entry_url
**場所**: `app/event_calendar/calendar_utils.py`

VRCイベントカレンダー投稿用のURLを生成。

### convert_markdown
**場所**: `app/event/libs.py`

MarkdownをHTMLに変換。

### extract_video_id
**場所**: `app/event/views.py:210`

YouTube URLから動画IDを抽出。

---

## テスト関数

### GenerateRecurringEventsCommandTest
**場所**: `app/event/tests/test_generate_recurring_events_command.py`

generate_recurring_eventsコマンドの包括的なテスト。

- 基本的なイベント生成
- ドライランオプション
- リセット機能
- 重複防止
- 各種定期ルールパターン