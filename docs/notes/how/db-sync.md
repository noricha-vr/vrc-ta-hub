# 本番DB↔ローカルDB同期 手順

## 前提条件

- `.env.production.local` に本番DB接続情報（`DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`）
- ローカル: MariaDB（`db` コンテナ、`my_network` 上の外部コンテナ）
- 本番: AWS RDS MySQL

## 本番 → ローカル

```bash
# 1. ダンプ取得
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
mysqldump --skip-ssl --no-tablespaces \
  -h $DB_HOST -u $DB_USER -p$DB_PASSWORD $DB_NAME \
  --single-transaction \
  | gzip > dumps/${TIMESTAMP}_production.sql.gz

# 2. ローカルDBにリストア（dbコンテナ経由）
gunzip -c dumps/${TIMESTAMP}_production.sql.gz \
  | docker exec -i db mysql -u root -proot local_vrc_ta_hub

# 3. マイグレーション適用
docker compose exec vrc-ta-hub python manage.py migrate
```

## 注意点

### mysqldump のハマりポイント
- `--no-tablespaces` 必須: RDS では PROCESS 権限がなく、省略すると `Access denied` エラー
- `--set-gtid-purged=OFF` は MariaDB では不要（MySQL 専用オプション、MariaDB だと `unknown variable` エラー）
- `--skip-ssl`: RDS への接続時に必要

### リストア先の特定
- `db` コンテナはこのプロジェクトの docker-compose.yaml には定義されていない
- `my_network` 上の外部コンテナ（他プロジェクトと共有）
- `docker exec -i db mysql ...` でアクセス（`docker compose exec` ではない）
- アプリコンテナ（vrc-ta-hub）には mysql クライアントが入っていない
