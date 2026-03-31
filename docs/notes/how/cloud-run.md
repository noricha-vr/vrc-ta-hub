# Cloud Run 本番切替パターン

## vrc-ta-hub 本番デプロイ後の確認
- 問題: `cloudbuild.yaml` が `--no-traffic` 付きなので、本番ビルド成功だけでは `latestReadyRevision` が増えるだけで公開トラフィックは切り替わらない
- 解決:
  1. `gcloud run services describe vrc-ta-hub --project=vrc-ta-hub --region=asia-northeast1 --format='yaml(spec.traffic,status.latestReadyRevisionName,status.latestCreatedRevisionName)'` で新旧リビジョンを確認する
  2. 新リビジョンが `Ready=True` なら `gcloud run services update-traffic vrc-ta-hub --project=vrc-ta-hub --region=asia-northeast1 --to-revisions=<REVISION>=100` で切り替える
  3. 切替後は `https://vrc-ta-hub.com/` と必要な API を `curl` で確認する
- 教訓: `rev-*` の tagged URL は Django の `ALLOWED_HOSTS` に含まれず `DisallowedHost` になるので、公開ドメインの疎通確認を正として扱う

## toGithubPagesJson 再生成
- 問題: VRChat ワールド表示用 JSON は `noricha-vr/toGithubPagesJson` 側の GitHub Actions が生成しており、`vrc-ta-hub` 本番反映だけでは更新されない
- 解決:
  1. `gh workflow run 'Scheduled Data Fetch' --repo noricha-vr/toGithubPagesJson`
  2. `gh run watch <RUN_ID> --repo noricha-vr/toGithubPagesJson`
  3. `curl -fsS 'https://noricha-vr.github.io/toGithubPagesJson/sample.json?ts=$(date +%s)' | jq ...` で件数・ジャンル値・不要データ混入を確認する
- 教訓: `sample.json` の再確認ではキャッシュ回避クエリを付けると公開反映を即確認しやすい
