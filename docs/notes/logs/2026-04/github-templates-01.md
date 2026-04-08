## GitHub PR / Issue テンプレートを標準化
- 日付: 2026-04-09
- 関連: なし
- 状況: `vrc-ta-hub` に `github-template-setup` を適用し、テンプレート整備を `main` へ直接反映したかった
- 問題: 既存の `.github/pull_request_template.md` がスキル標準と異なり、Issue テンプレートも未作成だった
- 対応: `--dry-run` で衝突確認後、`--force --no-commit` で PR / Issue テンプレートを反映し、運用メモを `docs/notes` に追記した
- → how/github-templates.md に知識として追記済み
