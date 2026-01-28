# デプロイ・運用パターン

## 本番環境への接続

### サーバー起動

```bash
# ドライラン（設定確認）
docker compose -f docker-compose-production.yaml config

# 起動
docker compose -f docker-compose-production.yaml up -d --build

# 状態確認
docker compose -f docker-compose-production.yaml ps
docker logs vrc-ta-hub --tail 20
```

### 環境変数

- `.env.production.local` から読み込み
- `DEBUG=False` が設定される
- 本番DB（AWS RDS）に接続

---

## マイグレーション手順

### 1. ドライラン（必須）

```bash
docker exec vrc-ta-hub python manage.py migrate --plan
```

**確認ポイント:**
- 適用されるマイグレーションの一覧
- 破壊的変更（フィールド削除、型変更）がないか
- 依存関係の順序

### 2. 実行

```bash
docker exec vrc-ta-hub python manage.py migrate
```

### 3. 確認

```bash
docker exec vrc-ta-hub python manage.py showmigrations
```

---

## 静的ファイルのデプロイ

```bash
# Cloudflare R2にアップロード（DEBUG=False必須）
docker compose exec -e DEBUG=False vrc-ta-hub python manage.py collectstatic --noinput

# 確認
curl -sI "https://data.vrc-ta-hub.com/[パス]"
```

---

## トラブルシューティング

### コンテナが起動しない

```bash
# ログ確認
docker logs vrc-ta-hub

# 直接シェルに入る
docker run -it --rm vrc-ta-hub-vrc-ta-hub /bin/bash
```

### マイグレーションエラー

```bash
# 特定のマイグレーションを fake
docker exec vrc-ta-hub python manage.py migrate --fake app_name migration_name

# マイグレーション状態確認
docker exec vrc-ta-hub python manage.py showmigrations
```

---

## チェックリスト

### デプロイ前

- [ ] ローカルでテスト通過
- [ ] PR マージ済み
- [ ] マイグレーションファイルがコミットされている

### デプロイ時

- [ ] `docker compose config` でドライラン
- [ ] `migrate --plan` でマイグレーション確認
- [ ] サーバー起動後のログ確認

### デプロイ後

- [ ] 動作確認（主要機能のスモークテスト）
- [ ] エラーログの監視

---

*初出: 2026-01-28 運用手順の整理*
