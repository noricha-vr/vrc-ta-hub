---
description: 静的ファイル編集時のCloudflare R2へのアップロード手順
globs: ["app/*/static/**/*"]
---

# 静的ファイルのデプロイ

## 本番環境への反映手順

静的ファイル（画像、CSS、JSなど）を追加・編集した場合、Cloudflare R2へのアップロードが必要。

```bash
# DEBUG=False で collectstatic を実行（R2にアップロード）
docker compose exec -e DEBUG=False vrc-ta-hub python manage.py collectstatic --noinput
```

## 確認方法

```bash
# アップロードされたファイルにアクセスできるか確認
curl -sI "https://data.vrc-ta-hub.com/[パス]"
```

## 注意事項

- 開発環境（DEBUG=True）では `collectstatic` はローカルにコピーされる
- 本番反映には必ず `DEBUG=False` を指定すること
- `.env.local` にR2認証情報（AWS_ACCESS_KEY_ID等）が必要
