# VRC技術学術ハブ ドキュメント

VRC技術学術ハブの開発、運用、調査メモ、利用ガイドをまとめた入口です。実在するドキュメントへ絞って導線を整理しています。

## 開発・運用の基本

- [セットアップ](setup.md)
- [デプロイ](deployment.md) — [ヘルスチェック](deployment.md#health)
- [テスト方針と共有 factory ヘルパー](testing.md)
- [関数リファレンス](functions.md)
- [管理スクリプト一覧](../scripts/index.md)
- [次にやるべきこと](next-actions.md)
- [プロンプトメモ](prompt.md)
- [テストカバレッジ](coverage.md)
- [構造化ログ (structlog)](logging.md)

## 主要仕様と運用ドキュメント

- [イベント定期登録とGoogleカレンダー連携システム](event_recurrence_system.md)
- [定期イベント管理システム](recurring_events_management.md)
- [定期イベントの削除・日付移動の例外管理](event-occurrence-tombstones.md)
- [Googleカレンダー同期システム](google_calendar_sync.md)
- [Googleカレンダー同期の重複問題解決報告](google_calendar_sync_issue_resolved.md)
- [Google Calendar同期の重複問題 詳細分析](google_calendar_sync_analysis.md)
- [静的ファイルのCloudflare R2同期手順](static_files_sync.md)
- [マイグレーション Rollback 手順](migration-rollback.md)
- [Vketコラボ現行仕様](vket-collaboration-spec.md)
- [Discord Bot仕様書](discord-bot-specification.md)
- [Discord Bot実装ガイド](discord-bot-implementation-guide.md)

## ガイドとナレッジ

- [利用ガイド](guide/index.md)
- [スライドをVRChatで映す手順](guide/speaker/slide-video.md)
- [要件定義・仕様メモ](requirements/index.md)
- [提案書](proposals/index.md)
- [Giga Week 2025 Winter 下書き](giga-week-2025-winter/index.md)

## 分析・調査メモ

- [調査・分析レポート集](research/index.md) — リファクタリング計画ほか
- [Issue #522 X-Forwarded-Proto 調査](research/issue-522-x-forwarded-proto.md)
- [Issue #513 LLM 向け Markdown エンドポイント](research/issue-513-llm-markdown-endpoints.md)
- [コミュニティ開催パターン分析（2025年6月）](community_schedule_analysis_2025_06.md)
- [Django 5.2 移行計画](django-5.2-migration-plan.md)
- [Django 5.2 移行前 deprecation warning 調査](django-5.2-warning-cleanup.md)
- [Issue #135 EventDetail 日時ロック調査メモ](issue-135-eventdetail-datetime-lock.md)
- [Issue #228 調査メモ](issue-228-approval-message.md)
- [Issue #280 ブログサムネイル・SNS共有画像の調査結果](issue-280-slide-thumbnail-share.md)
- [Issue #288 Vket確定後の主催者向け日程・LTロック](issue-288-vket-confirmed-lock.md)
- [Issue #292 db-pull の Docker Compose DB 復元検証](issue-292-db-pull-compose-restore.md)
- [Issue #343 LT申請「追加情報」テンプレート初期値化](research/issue-343-lt-application-additional-info-initial.md)
