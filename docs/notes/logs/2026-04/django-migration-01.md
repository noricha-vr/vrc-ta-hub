## VketParticipation の欠損カラムを修復
- 日付: 2026-04-05
- 関連: #171
- 状況: `VketParticipation.stage_registered_at` を前提にした参加状況画面と関連APIが本番で落ちていた
- 問題: `vket_participation` テーブルに `stage_registered_at` が存在せず、ORMのSELECT時点で `Unknown column` が発生していた
- 対応: `vket` に保険migrationを追加し、`stage_registered_at` が欠損しているDBだけ列を追加するようにした。あわせて欠損時だけSQLを発行する回帰テストを追加した
- → how/django-migration.md に知識として追記済み
