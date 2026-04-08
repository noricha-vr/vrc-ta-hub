## 資料公開Webhookを slide_share シグナルに統合した
- 日付: 2026-04-08
- 関連: #233
- 状況: 登壇者がLTスライドをアップロードした際に、集会ごとの Discord Webhook へ通知したかった。
- 問題: 資料公開ツイートは既に `slide_share` シグナルで発火していた一方、Webhook通知を別ビューへ実装すると条件ズレや二重送信が起きやすかった。
- 対応: `event.notifications.notify_slide_material_published` を追加し、`twitter.signals._queue_slide_share_tweet` から呼ぶ構成にした。通知対象は `slide_url` / `slide_file` の初回設定時だけに絞り、YouTube 単独追加では通知しないようにした。
- → how/event-webhook.md に知識として追記済み
