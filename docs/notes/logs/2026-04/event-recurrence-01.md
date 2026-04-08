## UIUXデザイン集会の future event ドリフトを固定日ルールで自己回復させた
- 日付: 2026-04-08
- 関連: #230
- 状況: `UIUXデザイン集会` の future event が本番DBで過剰登録され、Cloud Scheduler の `/event/generate/` 実行のたびに 11 日以外の日付が増えていた
- 問題: `毎月11日` が `frequency=OTHER` + LLM 生成のまま運用されていて、誤った future instance が次回基準日を押し流していた
- 対応: `毎月N日` を deterministic に生成するよう修正し、ルール外の future instance を除去してから次回基準日を計算するようにした
- → how/event-recurrence.md に知識として追記済み
