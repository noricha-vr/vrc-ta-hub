# スクリプト一覧

このディレクトリには、VRC技術学術ハブのイベント管理に関する各種スクリプトが含まれています。

## ⚠️ 使用上の注意

- これらのスクリプトは直接データベースやGoogleカレンダーを操作します
- 実行前に必ず環境変数（.env.local）が正しく設定されていることを確認してください
- 本番環境での実行は慎重に行ってください
- バックアップを取ってから実行することを推奨します

## 📊 分析・調査スクリプト

### パターン分析
- `analyze_biweekly_pattern.py` - 隔週開催パターンの分析
- `biweekly_pattern_summary.py` - 隔週パターンのサマリー表示
- `analyze_wrong_weekday_events.py` - 曜日が間違っているイベントの分析
- `check_weekdays_consistency.py` - 曜日の一貫性チェック
- `display_all_recurrence_patterns.py` - 全ての繰り返しパターンを表示

### 同期状況確認
- `analyze_sync_issue.py` - 同期問題の分析
- `check_sync_status.py` - 同期ステータスの確認
- `check_event_consistency.py` - イベントの一貫性チェック
- `final_status_check.py` - 最終ステータスチェック

### 重複チェック
- `check_duplicates_only.py` - 重複のみをチェック
- `investigate_massive_duplicates.py` - 大量重複の調査
- `investigate_google_calendar_duplicates.py` - Googleカレンダーの重複調査

### Googleカレンダー調査
- `check_google_calendar_future_events.py` - 将来のイベント確認
- `list_google_calendar_events.py` - Googleカレンダーイベント一覧

### 特定コミュニティ調査
- `check_vr_cafe.py` - VR研究CafeのGoogleカレンダー情報取得（エラーあり）
- `check_vr_cafe_django.py` - Django環境用VR研究Cafe情報取得（エラーあり）

## 🗑️ クリーンアップ・削除スクリプト

### 重複削除
- `remove_google_calendar_duplicates.py` - Googleカレンダーの重複削除
- `remove_google_duplicates.py` - Google側の重複削除
- `remove_remaining_duplicates.py` - 残りの重複削除
- `final_duplicate_cleanup.py` - 最終的な重複クリーンアップ

### イベント削除
- `delete_future_events.py` - 将来のイベント削除
- `delete_far_future_events.py` - 遠い将来のイベント削除
- `delete_wrong_weekday_events.py` - 曜日が間違っているイベント削除
- `delete_all_google_events.py` - 全Googleイベント削除（⚠️危険）

### クリーンアップ
- `cleanup_orphaned_events.py` - 孤立したイベントのクリーンアップ
- `final_cleanup.py` - 最終クリーンアップ
- `final_cleanup_and_sync.py` - 最終クリーンアップと同期
- `clean_and_resync.py` - クリーンアップと再同期
- `complete_reset.py` - 完全リセット（⚠️危険）

## 🔄 同期・再作成スクリプト

### 基本同期
- `sync_to_google_calendar.py` - Googleカレンダーへの基本同期
- `simple_sync.py` - シンプルな同期処理
- `force_complete_sync.py` - 強制完全同期

### 特定コミュニティ同期
- `sync_community_79.py` - コミュニティID:79の同期
- `sync_remaining_events.py` - 残りのイベント同期

### イベント再作成
- `recreate_all_events.py` - 全イベント再作成
- `recreate_future_events.py` - 将来のイベント再作成
- `recreate_future_events_final.py` - 将来のイベント最終再作成
- `create_missing_events.py` - 欠落イベントの作成

### 同期ロジック修正
- `fix_sync_logic.py` - 同期ロジックの修正

## 📅 イベント生成・更新スクリプト

### カスタムイベント生成
- `generate_custom_events.py` - カスタムルールでのイベント生成
- `generate_events_for_community_79.py` - コミュニティID:79のイベント生成
- `test_event_generation.py` - イベント生成のテスト

### 繰り返しパターン更新
- `update_recurrence_patterns.py` - 繰り返しパターンの更新
- `remove_uncertain_recurrence.py` - 不確定な繰り返しパターンの削除
- `remove_biweekly_ab.py` - 隔週A/Bパターンの削除

### 隔週パターン更新
- `update_biweekly_patterns.py` - 隔週パターンの更新
- `update_remaining_biweekly.py` - 残りの隔週パターン更新
- `update_final_biweekly.py` - 最終的な隔週パターン更新

### 特殊ルール更新
- `update_community_79_weekly.py` - コミュニティID:79を週次に更新
- `update_irregular_schedules.py` - 不規則スケジュールの更新
- `update_irregular_schedules_v2.py` - 不規則スケジュール更新v2

### 日付関連修正
- `fix_start_dates.py` - 開始日の修正
- `populate_start_dates.py` - 開始日の設定
- `update_start_date_from_recent_events.py` - 最近のイベントから開始日を更新
- `fix_monthly_events.py` - 月次イベントの修正

## 📊 エクスポート・レポート

- `export_active_communities.py` - アクティブなコミュニティのエクスポート
- `active_communities_*.csv` - エクスポートされたコミュニティデータ
- `event_backup_*.json` - イベントのバックアップデータ

## 🧪 テスト・検証

- `test_event_generation.py` - イベント生成機能のテスト

## 📝 推奨される統合・削除

### 統合候補
1. **重複チェック系**: `check_duplicates_only.py`, `investigate_massive_duplicates.py`, `investigate_google_calendar_duplicates.py` を1つに統合
2. **重複削除系**: 4つの重複削除スクリプトを1つの包括的なスクリプトに統合
3. **隔週パターン更新**: 3つの隔週更新スクリプトを1つに統合

### 削除候補
1. `check_vr_cafe.py`, `check_vr_cafe_django.py` - エラーがあり動作しない
2. 一時的な修正スクリプト（fix_*, update_*）で既に適用済みのもの

## 🚀 よく使うスクリプト

### 日常的なメンテナンス
```bash
# 同期状況の確認
docker compose exec vrc-ta-hub python scripts/check_sync_status.py

# 重複チェック
docker compose exec vrc-ta-hub python scripts/check_duplicates_only.py

# シンプルな同期
docker compose exec vrc-ta-hub python scripts/simple_sync.py
```

### トラブルシューティング
```bash
# 同期問題の分析
docker compose exec vrc-ta-hub python scripts/analyze_sync_issue.py

# 重複の削除
docker compose exec vrc-ta-hub python scripts/remove_google_duplicates.py

# 将来のイベント確認
docker compose exec vrc-ta-hub python scripts/check_google_calendar_future_events.py
```

### カスタムイベント生成
```bash
# カスタムルールでイベント生成
docker compose exec vrc-ta-hub python scripts/generate_custom_events.py
```