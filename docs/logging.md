# 構造化ログ (structlog)

vrc-ta-hub は [structlog](https://www.structlog.org/) で構造化ログを統一している。
本番は JSON 形式で Cloud Logging に取り込みやすく、開発は ConsoleRenderer で
人間可読のキー=値形式で出力される。

## なぜ構造化ログか

- **検索・集計が楽**: Cloud Logging / BigQuery 上で `jsonPayload.user_id` の
  ように構造化フィールドで絞り込める。
- **LLM フレンドリー**: 1イベント 1 JSON 行で grep / 要約が安定する。
- **コンテキスト持ち回り**: `structlog.contextvars.bind_contextvars(...)` で
  request_id / user_id を伝播でき、ログ呼び出しごとに引数を渡さなくて済む。

## 既存パターンは無変更で動く

`app/website/settings/base.py` で stdlib logging と structlog を
`ProcessorFormatter` 経由で統合しているため、**既存の `logger.info(...)` /
`logger.error(...)` 形式は無変更で動作する**。

```python
import logging
logger = logging.getLogger(__name__)

# 旧来通り。f-string も printf 形式もそのまま。
logger.info(f'User created: {user.id}')
logger.info('User %s logged in', user.user_name)
logger.error('Failed to sync calendar: %s', exc, exc_info=True)
```

出力例 (DEBUG=False / JSON):

```json
{"event": "User created: 7", "level": "info", "logger": "user_account.views",
 "timestamp": "2026-06-09T22:10:33+09:00"}
```

出力例 (DEBUG=True / ConsoleRenderer):

```
2026-06-09T22:10:33+09:00 [info     ] User created: 7 [user_account.views]
```

## 新しいパターン: 構造化フィールド付与

新規コードでは `structlog.get_logger()` を使ってキーワード引数で
コンテキストを付与すると、JSON フィールドとして残せる。

```python
import structlog
log = structlog.get_logger(__name__)

log.info('user.login', user_id=user.id, source='discord_oauth')
log.warning('calendar.sync.partial_failure', event_count=len(events), missing=5)
```

JSON 出力:

```json
{"event": "user.login", "user_id": 7, "source": "discord_oauth",
 "level": "info", "logger": "user_account.views",
 "timestamp": "2026-06-09T22:10:33+09:00"}
```

### event 名の付け方

- 動詞ベースの短い英語 (`user.login`, `calendar.sync.start`,
  `eventdetail.generate.completed`) を推奨。
- ドメインを `.` でドット連結して階層化すると Cloud Logging 検索が楽。
- 機密情報 (パスワード / トークン / API キー) を**フィールドに含めない**。

### request-scoped コンテキストの伝播

```python
import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

def view(request):
    clear_contextvars()
    bind_contextvars(request_id=request.headers.get('X-Request-Id'),
                     user_id=request.user.id if request.user.is_authenticated else None)
    # この view 配下のあらゆる log.info(...) に request_id / user_id が
    # 自動で付与される
    ...
```

## GCP Cloud Logging との連携

Cloud Run 上の stdout はそのまま Cloud Logging に取り込まれる。
JSON 行は `jsonPayload` として展開されるため、ログエクスプローラで:

```
jsonPayload.event="user.login" AND jsonPayload.user_id=7
```

のような検索ができる。`severity` への変換が必要な場合は、本番 LOGGING に
`structlog.processors.add_log_level` を追加して `severity` キーに
リマップする処理を追加できる (現状は `level` キーで運用)。

## テスト互換性

`unittest.TestCase.assertLogs(...)` / `pytest.caplog` はどちらも
`logging.LogRecord` ベースで動くため、`logger.info("msg")` の
`record.getMessage()` がそのまま比較対象になる。structlog 化後も既存テスト
(`assertLogs("event", level="ERROR")` パターン) はそのまま pass する。

## 参考

- structlog 公式: <https://www.structlog.org/>
- Django との統合パターン: `structlog.stdlib.ProcessorFormatter` を
  `LOGGING['formatters']` で使うのが標準。
