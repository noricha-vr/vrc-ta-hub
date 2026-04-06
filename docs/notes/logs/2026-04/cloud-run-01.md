## Cloud Run の古い tagged revision URL を cleanup する
- 日付: 2026-04-05
- 関連: #173
- 状況: `DisallowedHost` のアラートを調べたところ、Django 側の preview host 正規化はすでに入っていたのに、古い `rev-24d1224---...a.run.app` がまだ 400 を返していた。
- 問題: `cloudbuild.yaml` が本番デプロイごとに `--tag rev-$SHORT_SHA` を付けていたため、修正前の revision URL が Cloud Run 上に残り続けていた。
- 対応:
  1. `cloudbuild.yaml` から SHA 固定 tag を外した
  2. `gcloud run services update-traffic ... --update-tags=preview=LATEST` で preview URL を1本に集約するよう変更した
  3. 既存 `rev-*` tag を `--remove-tags` で cleanup するステップを追加した
  4. `website.tests.test_cloudbuild_config` を追加し、CI でも tag 運用を検証するようにした
- → how/cloud-run.md に知識として追記済み
