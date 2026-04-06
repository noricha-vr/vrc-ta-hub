## CI テスト対象拡大時の外部API安定化
- 日付: 2026-04-06
- 関連: #195, #209
- 状況: GitHub Actions の CI 対象を `event` `twitter` `ta_hub` `vket` など主要アプリへ広げようとした
- 問題: `event` の定期ルール系テストが OpenRouter / LLM 前提になっていて、API キー未設定やダミー値の CI 環境で落ちた
- 対応: `RecurrenceService` を遅延初期化へ変更し、実 API テストは `RUN_EXTERNAL_API_TESTS=1` + 非ダミーキー時のみ実行、API レイヤーの検証はモックに切り替えた
- → how/ci-testing.md に知識として追記済み
