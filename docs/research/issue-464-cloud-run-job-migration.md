# Issue #464 Cloud Run Job migration 調査

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
- 両方とも `manage.py migrate`、`gcloud run jobs deploy`、`gcloud run jobs execute` のいずれも含まない。
- 既存の `docs/migration-rollback.md` には、緊急 rollback の選択肢として `gcloud run jobs execute vrc-ta-hub-migrate` が記載されている。ただし、CI/CD の通常経路へ組み込まれてはいない。
- `app/website/tests/test_cloudbuild_config.py` は Cloud Run revision tag 運用の制約のみを検査しており、migration 実行順序は検査していない。
- この作業では、保護対象ファイルである `cloudbuild.yaml` / `cloudbuild-dev.yaml` を変更していない。

## 原因候補

本番事故の構造的な原因は、Cloud Run Service の新 revision 作成前に DB schema を同期する自動ステップがないこと。

Django 側の view や model に前方互換コードを足すだけでは、次のような schema drift を一般には解消できない。

- 新規カラム参照: ORM query が未作成カラムを SELECT して失敗する。
- NOT NULL / index / constraint 追加: DB 側状態がコードの前提と一致しない。
- data migration: コードだけでは既存データの変換完了を保証できない。

そのため、今回の根本対応はアプリケーションコードではなく CI/CD の migration 実行順序に置く必要がある。

## 推奨方針

Cloud Build 内で短命の Cloud Run Job を deploy/execute し、migration 成功後に Service deploy へ進む構成にする。

想定順序:

1. Kaniko で migration 対象と同一の `$SHORT_SHA` イメージを build/push する。
2. `vrc-ta-hub-migrate` Job をそのイメージで更新する。
3. Job で `python manage.py migrate --noinput` を実行し、完了を待つ。
4. migration が成功した場合だけ Cloud Run Service を deploy する。

この順序にすると、migration 失敗時は Cloud Build が失敗し、新しい Service revision の作成または traffic 切替に進まない。旧 revision を維持できるため、未適用 schema のまま新コードが動く事故を避けやすい。

## 必要な設定変更

次の変更が必要。ただし、いずれも今回の作業で変更禁止と指定されたファイルまたはインフラ権限に関わるため、このコミットでは実装していない。

- `cloudbuild.yaml` に本番用 `vrc-ta-hub-migrate` Job の deploy/execute/wait ステップを追加する。
- `cloudbuild-dev.yaml` に dev 用 migration Job ステップを追加する。
- Job に Service と同じ DB 接続情報、Secret Manager secrets、必要な環境変数を渡す。
- Job の service account に Cloud SQL Client と Secret Accessor の権限があることを確認する。
- `app/website/tests/test_cloudbuild_config.py` に、Cloud Build が `migrate --noinput` を Service deploy より前に実行することを検査するテストを追加する。

## 却下した代替案

| 案 | 却下理由 |
|---|---|
| supervisor 起動時に `manage.py migrate --noinput` を実行 | 複数 revision/instance が同時起動したときに競合しやすく、migration 失敗時にも起動処理と混ざって原因追跡が難しい。`supervisor-app.conf` も今回の保護対象に近い起動設定で、変更リスクが高い。 |
| view/model 側で未適用カラムを避ける | `deleted_at` のような個別カラム事故は一時回避できても、将来の schema/data migration 全般を保証できない。 |
| 手動 migrate を運用手順に残す | 再発防止にならない。人間の実行忘れで同じ事故が起きる。 |

## 検証計画

保護対象ファイルの変更が許可された後、次の順で検証する。

1. Cloud Build dry run 相当の構文確認を行う。
2. dev の `cloudbuild-dev.yaml` で migration Job 作成から Service deploy まで通す。
3. 意図的に失敗する migration ブランチで、Service deploy がスキップされることを確認する。
4. 本番 Cloud Build で `--no-traffic` revision 作成前に migration Job が成功していることをログで確認する。
5. `showmigrations` で dev/prod DB の migration state が最新であることを確認する。

## 今回のコミットでの確認

- `cloudbuild.yaml` / `cloudbuild-dev.yaml` に `migrate`、`run jobs deploy`、`run jobs execute` が存在しないことを `rg` で確認した。
- `docs/migration-rollback.md` に Cloud Run Job 経由の手動 rollback 手順があることを確認した。
- ローカル環境には `gcloud` コマンドがなく、Cloud Run Job の実地作成確認は未実施。
