## Cloud Run デプロイで tweet_queue テーブル未作成のまま 500
- 日付: 2026-04-05
- 関連: #174
- 状況: `TweetQueue` を参照する本番画面が `MySQLdb.ProgrammingError: (1146, "Table 'vrc_ta_hub.tweet_queue' doesn't exist")` で落ちていた
- 問題: `cloudbuild.yaml` は Cloud Run リビジョンを作るだけで Django migration を実行しておらず、`tweet_queue` テーブルが本番 DB に作成されていなかった
- 対応:
  1. `docker-entrypoint.sh` を追加し、起動時に `python manage.py migrate_with_lock --noinput` を実行するよう変更
  2. `migrate_with_lock` 管理コマンドを追加し、MySQL advisory lock 付きで migration を直列化
  3. 管理コマンド・CLI パーサ・起動設定の回帰テストを追加
- → how/cloud-run.md に知識として追記済み
