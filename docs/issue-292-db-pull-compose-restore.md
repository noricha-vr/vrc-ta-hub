# Issue 292: db-pull の Docker Compose DB 復元検証

## 調査対象

- `Makefile` の `db-pull`
- `docker-compose.yaml` の `vrc-ta-hub` / `db` サービス設定
- `.env.local` のアプリコンテナ向け `DB_*` 設定
- `vket_collaboration` テーブルを使った復元後の代表件数確認

## 観測結果

- 既存の `db-pull` は `LOCAL_DB_HOST=127.0.0.1` と `LOCAL_DB_PORT=3306` を使って、ホスト側の MySQL に直接復元していた。
- Docker Compose のアプリコンテナは `DB_HOST=db` と `DB_NAME=local_vrc_ta_hub` を参照しており、ホスト側 `127.0.0.1:3306` とは別の復元先になり得る。
- `docker-compose.yaml` の `db` サービスは Compose ネットワーク上の `db:3306` として公開されるため、ホスト側の `DB_PORT` を変更してもアプリコンテナからの接続先は変わらない。

## 原因候補

`db-pull` が Docker Compose のサービス境界を経由せず、Makefile 内の固定ホスト・固定ポートに復元していたことが主因。ホスト側で別の MySQL が起動している、または `DB_PORT` が変更されている場合でも、Makefile のコマンドが成功してしまうとアプリが参照するDBにはデータが入らない。

## 改善内容

- `db-pull` の復元処理を `scripts/db_pull_restore.sh` に分離した。
- 復元先DB名はアプリコンテナの `DB_NAME` から取得し、`DB_HOST` が復元対象の Compose DB サービス名と一致しない場合は失敗させる。
- MySQL への復元は `docker compose exec -T db` 経由で行い、認証情報もアプリコンテナの `DB_USER` / `DB_PASSWORD` に揃えた。
- ホスト側 `DB_PORT` や別 MySQL の状態に依存しないため、Makefile の固定値と Compose 実態のずれで silent success しない。
- 復元後にアプリコンテナの `python manage.py shell -c` から `vket_collaboration` の件数を取得し、既定で1件以上ない場合は失敗させる。

## 検証手順

静的検証:

```bash
bash -n scripts/db_pull_restore.sh
bash -n scripts/tests/test_db_pull_restore.sh
bash scripts/tests/test_db_pull_restore.sh
make -n db-pull
```

実環境検証:

```bash
docker compose up -d db vrc-ta-hub
make db-pull
```

期待結果:

- `make db-pull` の復元ログに Docker Compose の `db` サービスとアプリコンテナの `DB_NAME` が表示される。
- 復元後に `Verified via app container: local_vrc_ta_hub.vket_collaboration has N rows.` が表示される。
- `DB_HOST` が `db` 以外に変わっている、または代表テーブルが空の場合、`Done` 表示前に失敗する。
