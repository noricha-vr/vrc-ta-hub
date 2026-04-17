## 当日リマインドの Phase 0 復活と本番キュー再登録
- 日付: 2026-04-17
- 関連: #248
- 状況: 2026-04-15 以降の予約されるべき X 告知がキュー一覧に現れず、当日リマインドも欠けていた
- 問題: 2026-04-14 の変更で `post_scheduled_tweets` の Phase 0 が削除され、当日に保存が発生しないイベントでは `daily_reminder` が未作成になっていた
- 対応:
  1. `post_scheduled_tweets` に当日 `daily_reminder` 補完の Phase 0 を復活した
  2. PR #248 を作成して CI 通過後に merge した
  3. Cloud Build は手動実行時に `SHORT_SHA` が必要だったため、明示指定で再実行した
  4. 本番DBをバックアップ後に migrate を実行し、対象6件のキューを本番DBへ登録した
  5. その後、キューを正本にする方針で Phase 0 のセルフヒーリングは再度削除した
- → how/twitter-queue.md に知識として追記済み
