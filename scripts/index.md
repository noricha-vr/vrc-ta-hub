# スクリプト一覧

Django関係のスクリプトはカスタムコマンドとして、各アプリケーション内のカスタムコマンドとして実装、実行します。

## DB同期

- `scripts/db_pull_restore.sh`: `make db-pull` が取得した本番ダンプを Docker Compose の `db` サービスへ復元し、アプリコンテナ経由で代表テーブル件数を検証します。
- `scripts/tests/test_db_pull_restore.sh`: Docker Compose 呼び出しをモックして、復元先サービス固定・`DB_HOST` 不一致検知・代表テーブル件数検証を確認します。
