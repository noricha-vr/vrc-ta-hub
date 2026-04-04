# Djangoマイグレーション修復パターン

## migration履歴と実スキーマがズレた本番DBを自己修復する
- 問題: Djangoのmigration履歴上は `AddField` 済みでも、手動DDL漏れや復元差分で実DBにカラムが存在せず、ORMの通常SELECTで `Unknown column` が発生することがある
- 解決: 既存migrationを巻き戻さず、末尾に idempotent な修復migrationを追加して introspection で列有無を確認し、欠損時だけ `ALTER TABLE ... ADD COLUMN ...` を実行する
- 教訓: アプリ側で参照を外して逃げるより、次回 `migrate` でスキーマ自体を自己修復させたほうが再発しにくい
