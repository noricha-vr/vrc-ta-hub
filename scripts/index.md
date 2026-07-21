# スクリプト一覧

Django関係のスクリプトはカスタムコマンドとして、各アプリケーション内のカスタムコマンドとして実装、実行します。

## テスト

- `scripts/run_tests.sh`: 通常テストを外向き通信遮断下で実行します。実疎通は `--live-smoke` で明示します。
- `scripts/tests/test_run_tests.sh`: 引数あり通常実行もoffline境界を通り、live smokeだけが分離経路になることを検証します。

## DB同期

- `scripts/db_pull_restore.sh`: `make db-pull` が取得した本番ダンプを Docker Compose の `db` サービスへ復元し、アプリコンテナ経由で代表テーブル件数を検証します。
- `scripts/tests/test_db_pull_restore.sh`: Docker Compose 呼び出しをモックして、復元先サービス固定・`DB_HOST` 不一致検知・代表テーブル件数検証を確認します。
