# Cloud Run 運用パターン

## vrc-ta-hub 本番デプロイ後の確認
- 問題: `cloudbuild.yaml` が `--no-traffic` 付きなので、本番ビルド成功だけでは `latestReadyRevision` が増えるだけで公開トラフィックは切り替わらない
- 解決:
  1. `gcloud run services describe vrc-ta-hub --project=vrc-ta-hub --region=asia-northeast1 --format='yaml(spec.traffic,status.latestReadyRevisionName,status.latestCreatedRevisionName)'` で新旧リビジョンを確認する
  2. 新リビジョンが `Ready=True` なら `gcloud run services update-traffic vrc-ta-hub --project=vrc-ta-hub --region=asia-northeast1 --to-revisions=<REVISION>=100` で切り替える
  3. 切替後は `https://vrc-ta-hub.com/` と必要な API を `curl` で確認する
- 教訓: `rev-*` の tagged URL は Django の `ALLOWED_HOSTS` に含まれず `DisallowedHost` になるので、公開ドメインの疎通確認を正として扱う

## タグ付きリビジョンURLで `DisallowedHost` を防ぐ
- 問題: Cloud Run を `--tag rev-$SHORT_SHA` 付きでデプロイすると `rev-<sha>---...a.run.app` のリビジョンURLが発行されるが、Django の `ALLOWED_HOSTS` が独自ドメインだけだと確認アクセスで `DisallowedHost` が発生する。
- 解決: `ALLOWED_HOSTS` は正規ホストのまま維持し、Cloud Run のこのサービス向け preview host だけを middleware で `vrc-ta-hub.com` に正規化する。追加ホストが必要な場合だけ `ALLOWED_HOSTS` / `HTTP_HOST` 環境変数から明示的に取り込む。
- 教訓: `*.a.run.app` を広く許可すると他サービス由来の Host まで通してしまう。Cloud Run preview URL 対応は「サービス名で絞って正規ホストへ寄せる」方が安全。

## preview tag を1本に固定して古い revision URL を残さない
- 問題: Django 側で preview host を正規化しても、`cloudbuild.yaml` が `--tag rev-$SHORT_SHA` を使い続けると古い tagged revision URL が公開されたまま残り、修正前リビジョン由来の `DisallowedHost` が再発し続ける。
- 解決: デプロイ時の SHA 固定 tag をやめ、`gcloud run services update-traffic --update-tags=preview=LATEST` で stable な preview URL 1本へ集約する。あわせて `--remove-tags` で既存の `rev-*` tag を掃除し、設定をテストで固定する。
- 教訓: アプリ側の host 対応だけでは「昔の revision URL がまだ外に残る」問題は止まらない。Cloud Run の tag 運用もセットで閉じないと、古いリビジョンがエラー源として居残る。

## preview host は nginx 前段でも正規化する
- 問題: Django middleware で preview host を正規化していても、nginx が raw `Host` を upstream にそのまま流すと、middleware より前で host を参照する経路や設定差分に対して `DisallowedHost` の再発余地が残る。
- 解決: nginx の `map` で `vrc-ta-hub` / `vrc-ta-hub-dev` 向け Cloud Run preview host を `vrc-ta-hub.com` に写像し、`proxy_set_header Host` で upstream へは正規ホストを渡す。Django 側 middleware は後段の防御として残す。
- 教訓: Cloud Run preview URL 対応は Django だけで完結させず、proxy 前段でも raw host を止める二段構えにすると運用差分に強い。service 名を片系に固定すると dev 環境だけ再発するので、デプロイ先一覧とセットで見直す。

## preview host の service 名は明示リストで固定する
- 問題: Cloud Run preview host の判定を `K_SERVICE` のような実行環境依存に寄せると、nginx 側の正規化ルールと前提がズレたときに `DisallowedHost` が再発しやすい。
- 解決: Django middleware 側も `vrc-ta-hub` / `vrc-ta-hub-dev` を既定値に持つ service 名リストで preview host を判定し、nginx テストも同じ正規表現ソースを参照して整合を固定する。
- 教訓: Host 正規化の条件は proxy とアプリで別々に推測させず、同じ service 群を前提にテストで縛るほうが追跡しやすい。

## canonical host は helper で正規化して複数ヘッダ経路をまとめて潰す
- 問題: `APP_CANONICAL_HOST` や `HTTP_HOST` が URL / host:port 形式で入ると、`ALLOWED_HOSTS` と middleware の正規化先がズレやすく、`HTTP_HOST` 以外の `SERVER_NAME` / `X-Forwarded-Host` に raw host が残る経路も取りこぼしやすい。
- 解決: host 正規化 helper を settings / middleware で共通化し、Cloud Run preview host を検知したら `HTTP_HOST` / `HTTP_X_FORWARDED_HOST` / `SERVER_NAME` をまとめて canonical host へ寄せる。あわせて middleware を最上流に置き、下流 middleware より先に raw host を潰す。
- 教訓: Host 正規化は「どの値を canonical とみなすか」と「どのヘッダ経路を潰すか」を別実装にしないほうが再発しにくい。

## toGithubPagesJson 再生成
- 問題: VRChat ワールド表示用 JSON は `noricha-vr/toGithubPagesJson` 側の GitHub Actions が生成しており、`vrc-ta-hub` 本番反映だけでは更新されない
- 解決:
  1. `gh workflow run 'Scheduled Data Fetch' --repo noricha-vr/toGithubPagesJson`
  2. `gh run watch <RUN_ID> --repo noricha-vr/toGithubPagesJson`
  3. `curl -fsS 'https://noricha-vr.github.io/toGithubPagesJson/sample.json?ts=$(date +%s)' | jq ...` で件数・ジャンル値・不要データ混入を確認する
- 教訓: `sample.json` の再確認ではキャッシュ回避クエリを付けると公開反映を即確認しやすい
