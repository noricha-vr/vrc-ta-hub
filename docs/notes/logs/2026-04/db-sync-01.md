## 本番DBからローカルへの初回同期

- 日付: 2026-04-06
- 関連: なし
- 状況: 本番DBのデータをローカルに同期したかった
- 問題: db-sync スキルが Ghost Writer CMS (PostgreSQL) 専用で、vrc-ta-hub (MySQL) では使えなかった
- 対応:
  1. `mysqldump --no-tablespaces` でダンプ取得（`--set-gtid-purged=OFF` は MariaDB で未サポート）
  2. `docker exec -i db mysql` でリストア（アプリコンテナに mysql クライアントなし、db は外部コンテナ）
  3. `python manage.py migrate` で差分適用
- → how/db-sync.md に知識として追記済み
