# Issue #464 migration 自動適用の調査と「導入しない」決定

## 決定（2026-06-10 オーナー判断）

**Cloud Build への Django migration 自動適用は導入しない。**

理由: 自動実行は予期しないタイミングで本番 schema を変更し、migration 起因の事故が起きた際に被害が制御不能になるため。migration はデプロイ前に人間が判断して手動で適用する運用を正とする。

この決定により Issue #464（cloudbuild.yaml への自動適用ステップ追加）はクローズする。以下は調査時の事実関係と、却下した案の記録。

## 今回の対応範囲

Issue #464 の直接案は `cloudbuild.yaml` / `cloudbuild-dev.yaml` の変更を必要とするが、これらは CI/CD と本番デプロイに直結する保護対象ファイルである。加えて、上記のオーナー判断により自動 migration は導入しない。

そのため今回の変更は、Cloud Build の設定変更ではなく、次の 2 点に限定する。

1. 自動 migration を入れない判断と手動適用の運用を、この調査文書に残す。
2. `app/website/tests/test_cloudbuild_config.py` で、production/dev の Cloud Build 定義が Django migration を自動実行しないことを検知する。

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
- `app/website/tests/test_cloudbuild_config.py` は、Cloud Build 定義に `manage.py migrate` や `vrc-ta-hub-migrate` の自動 execute/deploy が入っていないことを確認する。

## 運用手順（正）

デプロイ前に migration の有無を確認し、あれば**人間が手動で適用してから**デプロイする。

1. デプロイ対象のブランチに新しい migration ファイルが含まれるか確認する（`git diff origin/main --stat -- '*/migrations/*'` など）。
2. 含まれる場合、デプロイ前に手動で migrate を実行する（`docs/migration-rollback.md` の Cloud Run Job 手順、または本番 DB への直接適用）。
3. `showmigrations` で適用状態を確認してから Cloud Run のデプロイ・トラフィック切替に進む。

再発防止は「自動化」ではなく、この手順をデプロイ時のチェックリストとして徹底することで行う。

## 検証方針

- Cloud Build 定義そのものは保護対象のため変更しない。
- 既存の設定テストに、production/dev の Cloud Build が migration を自動実行しないことを追加する。
- テストで「自動 migration が入っていないこと」と「この判断記録への参照」を残し、将来の変更時に意図的な設計判断が必要になるようにする。

## 却下した案

| 案 | 却下理由 |
|---|---|
| Cloud Build 内で Cloud Run Job を deploy/execute し migration 成功後に Service deploy（Issue #464 の当初案） | 自動実行は事故時の被害が制御不能になる。migration 適用は人間の判断を挟む（オーナー判断）。 |
| supervisor 起動時に `manage.py migrate --noinput` を実行 | 同上に加え、複数 revision/instance の同時起動で競合しやすく、失敗時に起動処理と混ざって原因追跡が難しい。 |
| view/model 側で未適用カラムを避ける | `deleted_at` のような個別カラム事故は一時回避できても、将来の schema/data migration 全般を保証できない。 |
