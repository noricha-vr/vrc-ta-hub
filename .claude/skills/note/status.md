# Status

## 最終更新
2026-01-28 20:10

## 現在のフォーカス
LT申請機能の本番デプロイ完了、バックログ整理

## 進行中タスク
- [x] LT申請機能の実装
  - [x] LTApplicationCreateView 実装
  - [x] LTApplicationListView 実装
  - [x] Discord通知設定（Webhook URL）
  - [x] settings.html に外部連携セクションとして移動
  - [x] 画像パス多重ネスト問題の修正
  - [x] PR #42 マージ
  - [x] 本番環境デプロイ（docker-compose-production.yaml）
  - [x] 本番DBマイグレーション完了

## コンテキスト（重要な前提）
- PR #42 は main にマージ済み
- 本番環境にデプロイ済み（ポート8015）
- バックログを `docs/notes/todo.md` に統合

## 次にやること
バックログから選択（`docs/notes/todo.md` 参照）:
- P1: Twitter告知機能の説明充実、LT応募者向け導線
- P2: ユーザー向けガイド、集会フォロー機能、通知機能
- P3: PWA対応

## ブロッカー・未解決
- なし

## 学んだこと（セッション中に発見）
- Django save() の update_fields はオーバーライドされた save() 内の処理には影響しない [昇格済み]
- ImageField.save() でファイル名にディレクトリパスが含まれていると upload_to と二重になる [昇格済み]
