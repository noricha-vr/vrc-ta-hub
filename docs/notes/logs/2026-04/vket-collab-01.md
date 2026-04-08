## 公開イベント一覧の Google カレンダー URL 生成をスキーマ不整合に強くした
- 日付: 2026-04-07
- 関連: #221
- 状況: 公開イベント一覧で Google カレンダー URL を生成する処理が承認済み `EventDetail` を毎回追加クエリしていた
- 問題: `VketParticipation.stage_registered_at` がない古い DB スキーマ環境で、公開ページが `Unknown column` 500 に巻き込まれていた
- 対応: prefetch 済み詳細の再利用を優先し、未 prefetch 時も `speaker` / `theme` だけを 1 クエリで読むように変更した
- → how/vket-collab.md に知識として追記済み
