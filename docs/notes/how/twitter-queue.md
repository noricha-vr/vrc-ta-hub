# Twitter Queue パターン

## キューを正本にする
- 問題: scheduler 側で missing な `daily_reminder` を補完すると、キュー作成責務が signal と scheduler に分散して挙動が追いづらくなる
- 解決: 当日の LT/SPECIAL 保存時に signal で `lt -> skipped` と `daily_reminder` を作り、`post_scheduled_tweets` は既存キューだけを処理する
- 教訓: キュー作成は保存時、19:00 バッチはキュー処理という責務分離を崩さない

## Cloud Build を手動実行する時は SHORT_SHA を明示する
- 問題: `cloudbuild.yaml` が `:$SHORT_SHA` を前提にしていると、手動 `gcloud builds submit` では deploy step の image path が壊れる
- 解決: `gcloud builds submit --substitutions=SHORT_SHA=$(git rev-parse --short HEAD)` を使う
- 教訓: trigger 前提の substitution を使う build は、手動実行パスもあわせて確認する
