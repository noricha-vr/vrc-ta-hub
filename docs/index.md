# VRC技術学術ハブ ドキュメント

VRC技術学術ハブの開発、運用、調査メモ、利用ガイドをまとめた入口です。実在するドキュメントへ絞って導線を整理しています。

## 開発・運用の基本

- [セットアップ](setup.md)
- [デプロイ](deployment.md) — [ヘルスチェック](deployment.md#health)
- [テスト方針と共有 factory ヘルパー](testing.md)
- [mypy + django-stubs 段階導入ガイド](mypy.md)
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
- [Vketコラボ現行仕様・公開同期運用](vket-collaboration-spec.md)
- [Discord Bot仕様書](discord-bot-specification.md)
- [Discord Bot実装ガイド](discord-bot-implementation-guide.md)

## ガイドとナレッジ

- [利用ガイド](guide/index.md)
- [スライドをVRChatで映す手順](guide/speaker/slide-video.md)
- [要件定義・仕様メモ](requirements/index.md)
- [提案書](proposals/index.md)
- [Giga Week 2025 Winter 下書き](giga-week-2025-winter/index.md)
- [Vket 2026 Summer 動画アーカイブ下書き](giga-week-2026-summer/index.md)

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

## その他のドキュメント

- [draft_article](giga-week-2025-winter/draft_article.md)
- [集会を閉鎖・再開する](guide/community/close.md)
- [集会を登録する](guide/community/create.md)
- [集会情報を編集する](guide/community/edit.md)
- [活動停止を通報する](guide/community/report.md)
- [集会設定](guide/community/settings.md)
- [Hub公式Xアカウントでの自動告知](guide/event/auto-post.md)
- [イベントを登録する](guide/event/create.md)
- [イベントを削除する](guide/event/delete.md)
- [よくある質問](guide/faq.md)
- [発表申請を承認・却下する](guide/lt/approve.md)
- [ブログ記事を自動生成する](guide/lt/auto-generate.md)
- [発表情報を登録する](guide/lt/create.md)
- [スタッフを招待する](guide/member/invite.md)
- [スタッフを削除する](guide/member/remove.md)
- [主催者を引き継ぐ](guide/member/transfer.md)
- [アセットを設置する](guide/promotion/asset.md)
- [ワールドにポスターを掲示する](guide/promotion/poster.md)
- [Discord Webhookを設定する](guide/settings/discord.md)
- [発表申請受付を設定する](guide/settings/lt-application.md)
- [お知らせ（ブログ）機能 提案書（改訂版）](proposals/oshirase-blog.md)
- [定期イベント生成の冪等性](recurring-events-idempotency.md)
- [機能別仕様](requirements/features/index.md)
- [LT申請機能 仕様書](requirements/features/lt-application.md)
- [LT申請カスタムフィールド機能 要件定義書](requirements/lt-application-custom-fields.md)
- [improve-loop 2026-06-09 セッション findings](research/improve-loop-2026-06-09-findings.md)
- [Issue 325 設定値集約調査](research/issue-325-config-constants.md)
- [Issue 326: except pass 可視化の調査と対応](research/issue-326-error-handling.md)
- [Issue 333: View/Form 巨大ファイル分割の調査と実装方針](research/issue-333-view-form-refactor.md)
- [Issue #354 テスト中の TweetQueue 本文生成スレッド抑制 調査メモ](research/issue-354-tweet-generation-test-threads.md)
- [Issue 356 OpenRouter HTTP-Referer 調査](research/issue-356-openrouter-http-referer.md)
- [Issue 372: 発表一覧の詳細リンクラベル変更](research/issue-372-my-list-article-label.md)
- [Issue 377: 集会詳細ページのアクセス解析カード表示順](research/issue-377-community-analytics-order.md)
- [Issue 396: 発表資料アップロード依頼メール](research/issue-396-material-upload-reminder.md)
- [Issue 407: LT 告知ポストの登壇者 X アカウント表示](research/issue-407-lt-tweet-speaker-x-account.md)
- [Issue #464 migration 自動適用の調査と「導入しない」決定](research/issue-464-cloud-run-job-migration.md)
- [Issue #499 disabled の参加希望日バリデーション調査](research/issue-499-disabled-vket-date-validation.md)
- [Issue #500 Campaign UTM validator migration](research/issue-500-analytics-campaign-migration.md)
- [Issue #529 offline runner と Docker network-none の遮断結果](research/issue-529-offline-network-none.md)
- [Issue #542 Google→DB 同期デッドコード削除](research/issue-542-google-to-db-sync-removal.md)
- [Sentry エラートラッキング](sentry.md)
