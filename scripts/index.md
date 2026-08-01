# スクリプト一覧

Django関係のスクリプトはカスタムコマンドとして、各アプリケーション内のカスタムコマンドとして実装、実行します。

## テスト

- `scripts/run_tests.sh`: 引数なしの通常suiteと指定test labelを同じ外向き通信遮断下で実行します。実疎通だけを `--live-smoke <profile> [test label]` で明示します。
- `scripts/run_live_smoke.py`: clean-envから固定profileのallowlist credentialだけを専用Composeへ渡します。Calendar鍵はrepository/build context外のabsolute pathだけをread-only mountします。
- `scripts/tests/test_run_tests.sh`: 引数あり通常実行もoffline境界を通り、live smokeだけが分離経路になることを検証します。
- `scripts/tests/test_run_live_smoke.py`: profile検証、credential非漏洩、専用containerの起動引数を検証します。

## 単発メンテナンス（Django）

`scripts/_script_bootstrap.py` の `setup_django()` を `main()` から呼び、成功=0 / 失敗=1 の exit code を返します。
進捗・診断は logging（stderr）へ出します。

- `scripts/check_event_schedule.py`: 30日先までのイベントを一覧表示し、重複イベントがあれば exit 1。
- `scripts/create_activity_posts.py` / `scripts/create_update_post.py` / `scripts/create_vket_posts.py`: News 記事の投入。カテゴリ不在・fixture 不在は exit 1。
- `scripts/fix_h1_duplicates.py` / `scripts/fix_inner_h1_tags.py`: 本文の H1 重複・内部 H1 の是正。
- `app/website/tests/test_script_exit_codes.py`: 上記スクリプトが exit code 契約（`sys.exit(main())` / print 不使用）を守っていることを検証します。

## DB同期

- `scripts/db_pull_restore.sh`: `make db-pull` が取得した本番ダンプを Docker Compose の `db` サービスへ復元し、アプリコンテナ経由で代表テーブル件数を検証します。
- `scripts/tests/test_db_pull_restore.sh`: Docker Compose 呼び出しをモックして、復元先サービス固定・`DB_HOST` 不一致検知・代表テーブル件数検証を確認します。
