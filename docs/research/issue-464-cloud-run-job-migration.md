# Issue #464 migration 自動適用の調査と「導入しない」決定

## 決定（2026-06-10 オーナー判断）

**Cloud Build への Django migration 自動適用は導入しない。**

理由: 自動実行は予期しないタイミングで本番 schema を変更し、migration 起因の事故が起きた際に被害が制御不能になるため。migration はデプロイ前に人間が判断して手動で適用する運用を正とする。

この決定により Issue #464（cloudbuild.yaml への自動適用ステップ追加）はクローズする。以下は調査時の事実関係と、却下した案の記録。

## 背景

2026-06-10 の本番反映で、アプリケーションコードが新しい Django migration を前提に起動した一方、本番 DB へ `manage.py migrate` が適用されておらず、`/event/list/` が 500 を返した。

事故時に未適用だった migration は次の 5 件。

| migration | 影響 |
|---|---|
| `community.0027_encrypt_notification_webhook_url` | webhook URL の Fernet 暗号化 |
| `event.0025_alter_eventdetail_detail_type` | `EventDetail.detail_type` の変更 |
| `event.0026_recurrencerule_last_generated_date` | `RecurrenceRule.last_generated_date` の追加 |
| `event.0027_eventdetail_deleted_at` | `EventDetail.deleted_at` の追加。`/event/list/` 500 の直接原因 |
| `user_account.0013_apikey_allowed_ips_apikey_expires_at_apikey_scope` | API key の期限・スコープ・許可 IP 追加 |

## 観測結果

- `cloudbuild.yaml` は Kaniko で `$SHORT_SHA` イメージを build/push した後、`gcloud run deploy --no-traffic` で本番 Service の新 revision を作る。
- `cloudbuild-dev.yaml` は Kaniko で `latest` イメージを build/push した後、`gcloud run deploy` で dev Service を更新する。
- 両方とも `manage.py migrate` を含まない。これは**意図された構成**（上記の決定どおり）。
- `docs/migration-rollback.md` に、Cloud Run Job `vrc-ta-hub-migrate` を手動 execute する手順が記載されている。

## 運用手順（正）

デプロイ前に migration の有無を確認し、あれば**人間が手動で適用してから**デプロイする。

1. デプロイ対象のブランチに新しい migration ファイルが含まれるか確認する（`git diff origin/main --stat -- '*/migrations/*'` など）。
2. 含まれる場合、デプロイ前に手動で migrate を実行する（`docs/migration-rollback.md` の Cloud Run Job 手順、または本番 DB への直接適用）。
3. `showmigrations` で適用状態を確認してから Cloud Run のデプロイ・トラフィック切替に進む。

再発防止は「自動化」ではなく、この手順をデプロイ時のチェックリストとして徹底することで行う。

## 却下した案

| 案 | 却下理由 |
|---|---|
| Cloud Build 内で Cloud Run Job を deploy/execute し migration 成功後に Service deploy（Issue #464 の当初案） | 自動実行は事故時の被害が制御不能になる。migration 適用は人間の判断を挟む（オーナー判断）。 |
| supervisor 起動時に `manage.py migrate --noinput` を実行 | 同上に加え、複数 revision/instance の同時起動で競合しやすく、失敗時に起動処理と混ざって原因追跡が難しい。 |
| view/model 側で未適用カラムを避ける | `deleted_at` のような個別カラム事故は一時回避できても、将来の schema/data migration 全般を保証できない。 |
