# Sentry エラートラッキング

本番環境で「silent failure」(`logger.exception(...)` で握りつぶされる例外) を可視化するための仕組み。

## 役割と既存ログとの関係

vrc-ta-hub のログは 2 系統で運用する。

| 系統 | 役割 | 保存先 |
|------|------|--------|
| structlog (JSON) | 全行ログ。Cloud Logging に取り込み、運用調査の主軸 | GCP Cloud Logging |
| **Sentry** | **ERROR 以上の例外イベントを集約。スタックトレース・Issue 化・通知** | Sentry プロジェクト |

structlog は「何が起きたかの履歴」、Sentry は「同じ例外が何回出ているか・誰が踏んだか」を見るためのもの。
両方を残すのは、ログ全文を Sentry に送ると料金が嵩むため。

## 設定手順

1. Sentry プロジェクトを作成 (Django 用)
2. プロジェクト > Settings > Client Keys から DSN をコピー
3. Cloud Run のシークレットに `SENTRY_DSN` を登録 (Secret Manager 経由)
4. デプロイ後、Sentry の Issues タブにエラーが流入することを確認

開発環境では `SENTRY_DSN=` のまま (空) にしておけば初期化されない。
`TESTING=True` / `DEBUG=True` の場合も自動でスキップされる (`app/website/settings/base.py`)。

## silent exception の整理ポリシー

ログレベルと「Sentry に上げるべきか」の対応:

| 状況 | ログレベル | 構造化フィールド | Sentry に送る |
|------|-----------|----------------|--------------|
| ユーザー操作の失敗で、再試行で復帰可能 | `WARNING` | `is_silent=False` | しない |
| バックグラウンド処理が握りつぶす例外 (silent failure) | `ERROR` | `is_silent=True` | **送る** |
| データ破損・致命的不整合 | `CRITICAL` | `is_silent=True` | **送る** |
| 既知の外部 API 一時障害 (リトライで吸う) | `WARNING` | `is_silent=False` | しない |

### 構造化ログでの書き方

silent failure として例外を握りつぶす箇所では、structlog の context フィールドに
`is_silent=True` と `event_type` を必ず付ける。これにより:

- Sentry 側で `is_silent:true` の Issue だけ抽出してアラート設定できる
- structlog 側でも `event_type` 別に絞り込みが効く

```python
import logging

logger = logging.getLogger(__name__)

try:
    do_something_risky()
except Exception as exc:
    logger.exception(
        "silent_failure",
        extra={
            "event_type": "recurrence_history_lookup_failed",
            "rule_id": rule.id,
            "is_silent": True,
        },
    )
    return fallback_value
```

`logger.exception("silent_failure", extra={...})` シグネチャを推奨する。
`logger.exception("自由文")` でも動くが、`extra` を渡すことで Sentry / GCP Logging
両方で機械的にフィルタしやすくなる。

## Sentry アラート推奨ルール

Sentry の Alerts > Issue Alerts で以下を最低限設定する。

| ルール名 | 条件 | 通知先 |
|---------|------|--------|
| 5xx 急増 | `event.type:error` が 5 分間に 10 件以上 | Discord (#alert) |
| silent failure 連発 | `is_silent:true` の Issue が 1 時間に 20 件以上 | Discord (#alert) |
| Webhook 失敗連発 | `event_type:*webhook*` の Issue が 10 分に 5 件以上 | Discord (#alert) |
| 新規 Issue | First seen の新規 Issue | Email (運用担当) |

Discord 通知は Sentry の `Discord` Integration を使うか、Webhook で
`DISCORD_WEBHOOK_URL` (運用 channel) に飛ばす。

## ローカルでの動作確認

```bash
# 一時的に DSN を入れて DEBUG=False で起動して送信確認 (本番 DSN は使わない)
docker compose exec -e DEBUG=False -e SENTRY_DSN=https://xxx@sentry.io/yyy \
    vrc-ta-hub python -c "
import sentry_sdk
print('VERSION:', sentry_sdk.VERSION)
sentry_sdk.capture_message('test from vrc-ta-hub local')
"
```

確認後、`.env.local` の `SENTRY_DSN=` を空に戻す。
