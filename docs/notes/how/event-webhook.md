# Event Webhook パターン

## 資料公開Webhookは slide_share シグナルに相乗りする
- 問題: 登壇者のスライド公開時に集会Webhookへ通知したいが、記事生成・資料公開ツイート・資料URL更新が別々に見えて実装起点が散りやすい。
- 解決: `twitter.signals._queue_slide_share_tweet` の発火条件を資料公開の単一ソースとして扱い、Webhook通知はそこで追加する。通知本体は `event.notifications` に置き、シグナル側は条件判定と呼び出しだけにする。
- 教訓: 「ツイートと同じタイミング」が要件なら、別のビューやフォームに処理を増やすより既存の資料公開シグナルへ寄せたほうが条件ズレを防げる。

## スライド通知は YouTube 単独追加と分ける
- 問題: `slide_share` ツイートは YouTube 単独追加でも発火するため、そのままWebhook通知を相乗りさせると「スライド公開通知」が動画追加だけでも飛んでしまう。
- 解決: Webhook通知は `slide_url` または `slide_file` が初めて設定されたときだけ送る。YouTube 単独追加ではツイートだけ作成し、Webhookは送らない。
- 教訓: 既存シグナルの条件を丸ごと再利用すると文言と実際の発火対象がズレることがある。通知種別の意味に合わせて一段だけ条件を絞る。

## Webhook内のファイルURLは絶対URLに正規化する
- 問題: `FileField.url` は `/media/...` の相対パスになることがあり、Discord通知先からそのまま開けない。
- 解決: Webhook payload に入れる前に `https://vrc-ta-hub.com` を前置して絶対URLへ正規化する。
- 教訓: アプリ内表示では相対URLで十分でも、外部通知では絶対URL化が必要。
