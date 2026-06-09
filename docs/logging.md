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

## ログレベル使い分け規約

レベル選択を統一して、Cloud Logging 上のアラート設定 / フィルタリング /
LLM での自動要約を安定させる。**「ユーザー入力起因のミス」と「サービス側の障害」
を `WARNING` と `ERROR` で明確に分ける**のが基本方針。

| レベル | 用途 | 例 |
|--------|------|---|
| `DEBUG` | 開発時の詳細トレース、本番では非表示 | リクエスト処理の中間結果、SQL クエリ |
| `INFO` | 正常系の主要イベント | ユーザー登録成功、外部 API 呼び出し成功、同期完了 |
| `WARNING` | 想定内の異常、ユーザー入力ミス、リトライ中、4xx 相当 | validation 失敗、404 Not Found、リトライ中、Discord Webhook 送信失敗（ユーザー設定の URL ミス） |
| `ERROR` | 想定外の障害、運用対応が必要、5xx 相当 | DB 接続失敗、必須環境変数欠落、外部 API の予期せぬ 5xx、最終的に処理が失敗 |
| `CRITICAL` | サービス停止級の障害 | 起動失敗、致命的なデータ破損 |

### 判断フロー

1. **HTTP ステータスで考える**: 4xx を返すなら `WARNING`、5xx を返すなら `ERROR`
2. **リトライ中か最終失敗か**: リトライ中の警告は `WARNING`、リトライ尽きて最終失敗なら `ERROR`
3. **ユーザー操作で直せるか**: ユーザー側の設定ミス・入力ミスは `WARNING`、サーバー側の障害は `ERROR`
4. **処理が継続するか**: 部分失敗で全体処理が継続するなら `WARNING`、処理全体が失敗するなら `ERROR`

### 具体例

```python
# OK: 4xx 相当（バリデーション失敗）は warning
logger.warning("Tweet text is empty")
logger.warning("Image exceeded max size: %d bytes", downloaded)
logger.warning("validation_failed", user_id=user.id, errors=errors)

# NG: 4xx を error にしない
logger.error("validation failed")

# OK: 5xx 相当（DB 接続失敗）は error
logger.error("database_unreachable", db=settings.DATABASES['default']['HOST'])
logger.error("IndexView degraded gracefully because the database was unavailable",
             exc_info=True)

# OK: リトライ中は warning、最終失敗は error
logger.warning("Retrying after transient database disconnect: %s", exc)  # リトライ中
logger.error("Service unavailable after reconnect: %s", exc)             # 最終失敗

# OK: 部分失敗（処理継続）は warning
logger.warning("GA4 poster_click report failed; continuing with page report only",
               exc_info=True)
```

### 例外時の `logger.exception` について

`try/except` ブロック内で例外を捕捉した場合は `logger.exception(...)` を
推奨する。`exc_info=True` 相当の挙動で **ERROR レベル**として記録され、
スタックトレースが自動的に付与される。
本規約の「warning に降格すべき」判断は `logger.error` のみが対象で、
`logger.exception` は例外発生時の標準パターンとして変更しない。

## テスト互換性

`unittest.TestCase.assertLogs(...)` / `pytest.caplog` はどちらも
`logging.LogRecord` ベースで動くため、`logger.info("msg")` の
`record.getMessage()` がそのまま比較対象になる。structlog 化後も既存テスト
(`assertLogs("event", level="ERROR")` パターン) はそのまま pass する。

## 参考

- structlog 公式: <https://www.structlog.org/>
- Django との統合パターン: `structlog.stdlib.ProcessorFormatter` を
  `LOGGING['formatters']` で使うのが標準。
