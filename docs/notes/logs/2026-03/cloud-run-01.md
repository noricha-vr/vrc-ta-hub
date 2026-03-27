## Cloud Run 本番切替と JSON 再生成を手動確認
- 日付: 2026-03-27
- 関連: #120
- 状況: `feat: 集会一覧JSON APIを追加 (#120)` を本番へ反映し、VRChat ワールド向け JSON が壊れていないことも確認したかった
- 問題: 本番 `cloudbuild.yaml` は `--no-traffic` デプロイのため、`vrc-ta-hub-24d1224` が作成済みでも公開トラフィックは旧リビジョンに残っていた。さらに JSON 公開は別リポジトリ `toGithubPagesJson` の workflow 依存だった
- 対応:
  1. `gcloud run services update-traffic vrc-ta-hub --project=vrc-ta-hub --region=asia-northeast1 --to-revisions=vrc-ta-hub-24d1224=100` で本番切替
  2. `https://vrc-ta-hub.com/` が 200、`/api/v1/community/gathering-list/` が 73 件、`/api/v1/community/?format=json` が 75 件を返すことを確認
  3. `gh workflow run 'Scheduled Data Fetch' --repo noricha-vr/toGithubPagesJson` を実行し、run `23650817445` 成功を確認
  4. `https://noricha-vr.github.io/toGithubPagesJson/sample.json` を再取得し、75 件・ジャンル値 `技術系/学術系/その他`・テストデータ混入 0 件を確認

## Cloud Run のリビジョンURLで `DisallowedHost` が発生
- 日付: 2026-03-27
- 関連: #129
- 状況: `vrc-ta-hub` の Cloud Run リビジョンURL `rev-24d1224---vrc-ta-hub-mhbhtr6sha-an.a.run.app` でアクセス時に 500 エラーが発生していた。
- 問題: `app/website/settings.py` の `ALLOWED_HOSTS` が独自ドメイン・localhost・`HTTP_HOST` 環境変数しか見ておらず、Cloud Run のタグ付きリビジョンURLを許可していなかった。
- 対応: `ALLOWED_HOSTS` の組み立てを関数化し、`.a.run.app` と `ALLOWED_HOSTS` 環境変数を扱えるように修正した。Cloud Run リビジョンURLを再現するテストも追加した。
- → how/cloud-run.md に知識として追記済み
