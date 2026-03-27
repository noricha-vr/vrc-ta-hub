# Cloud Run 運用パターン

## タグ付きリビジョンURLで `DisallowedHost` を防ぐ
- 問題: Cloud Run を `--tag rev-$SHORT_SHA` 付きでデプロイすると `rev-<sha>---...a.run.app` のリビジョンURLが発行されるが、Django の `ALLOWED_HOSTS` が独自ドメインだけだと確認アクセスで `DisallowedHost` が発生する。
- 解決: `ALLOWED_HOSTS` に Cloud Run のサフィックス `.a.run.app` を追加し、必要に応じて `ALLOWED_HOSTS` / `HTTP_HOST` 環境変数から追加ホストも取り込めるようにする。
- 教訓: Cloud Run の独自ドメイン運用でも、タグ付きリビジョンURLを使うなら Django 側の host 許可は別途用意する。個別のリビジョン名を列挙せず、固定サフィックスで扱う。
