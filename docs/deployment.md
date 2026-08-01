# デプロイ

VRC技術学術ハブのデプロイ運用メモ。Cloud Run / Cloud Build を前提とする。

## Cloud Build Trigger のbranch filter

`cloudbuild.yaml` / `cloudbuild-dev.yaml` にbranch filterは定義できない。実行対象は
Google Cloud側のBuild Trigger設定で限定する。GitHub Actionsの`safe-to-test` labelは
テスト実行の承認だけで、build / deployの承認には使わない。

- production Triggerのbranch filterはexact `^main$` とする
- dev TriggerをGitHub pushに接続する場合も、レビュー済みの専用branchだけをexact matchで
  allowlistする。専用branchがない場合はmanual buildとし、wildcard Triggerを作らない
- `fix-flow/isolation-task-*`、PR head、その他のfeature branchをCloud Build Triggerにmatchさせない
- Trigger作成・変更後はGoogle Cloud側のbranch regexを読み戻し、isolation branchの
  pushでbuildが作られないことを確認する

GitHub Actionsの隔離PRゲートは[テスト方針](testing.md#github-actions-の隔離pr承認ゲート)を参照。
必要なTrigger filterを確認できない環境では、自動deployを有効化しない。

## ヘルスチェック {#health}

Cloud Run の readiness / liveness probe 用に `/health` エンドポイントを提供する。

| 項目 | 値 |
|------|-----|
| パス | `/health` |
| メソッド | GET |
| 認証 | 不要 |
| レスポンス（正常） | `200 OK` / `{"status":"ok","db":"ok","cache":"ok"}` |
| レスポンス（DB 障害） | `503 Service Unavailable` / `{"status":"ng","db":"ng", ...}` |

### 設計方針

- **DB の疎通失敗は致命的**: 503 を返してロードバランサから外す。zombie プロセスへの誤ルーティングを防ぐ。
- **cache 失敗は無視**: cache が未設定でも生存判定したいので、`cache=ng` でも `status=ok` を維持する。
- **軽量実装**: クエリは `connection.ensure_connection()` のみ、cache は短い TTL の往復確認のみ。

### 動作確認

```bash
# ローカル
curl -i http://localhost:8015/health

# 本番（Cloud Run）
curl -i https://vrc-ta-hub.com/health
```

### Cloud Run probe 設定例

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8000
  initialDelaySeconds: 30
  periodSeconds: 10
  failureThreshold: 3
```

## 関連ドキュメント

- [セットアップ](setup.md)
- [静的ファイルの Cloudflare R2 同期手順](static_files_sync.md)
