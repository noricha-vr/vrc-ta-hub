# Cloud Run 運用パターン

## タグ付きリビジョンURLで `DisallowedHost` を防ぐ
- 問題: Cloud Run を `--tag rev-$SHORT_SHA` 付きでデプロイすると `rev-<sha>---...a.run.app` のリビジョンURLが発行されるが、Django の `ALLOWED_HOSTS` が独自ドメインだけだと確認アクセスで `DisallowedHost` が発生する。
- 解決: `ALLOWED_HOSTS` は正規ホストのまま維持し、Cloud Run のこのサービス向け preview host だけを middleware で `vrc-ta-hub.com` に正規化する。追加ホストが必要な場合だけ `ALLOWED_HOSTS` / `HTTP_HOST` 環境変数から明示的に取り込む。
- 教訓: `*.a.run.app` を広く許可すると他サービス由来の Host まで通してしまう。Cloud Run preview URL 対応は「サービス名で絞って正規ホストへ寄せる」方が安全。
