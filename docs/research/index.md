# 調査・分析レポート

機械的スキャンや手動調査の結果をまとめた、再利用可能な分析レポートの入口です。

## レポート一覧

- [Issue #499 disabled の参加希望日バリデーション調査](issue-499-disabled-vket-date-validation.md) — Event がない確定済み参加で発表・備考更新が失敗する原因と回帰テスト
- [Issue #500 Campaign UTM validator migration](issue-500-analytics-campaign-migration.md) — `Campaign.utm_source` / `utm_medium` の validator 定義と migration 履歴の差分、および `AlterField` migration の安全性確認
- [Issue #356 OpenRouter HTTP-Referer 調査](issue-356-openrouter-http-referer.md) — preview/staging URL が OpenRouter の `HTTP-Referer` に漏れないようにする調査と検証方針
- [Issue #343 LT申請「追加情報」テンプレート初期値化](issue-343-lt-application-additional-info-initial.md) — LT申請フォームのテンプレート表示を placeholder から initial に変更する調査と検証方針
- [Issue #464 migration 自動適用の調査と「導入しない」決定](issue-464-cloud-run-job-migration.md) — 本番 schema drift 事故の原因整理と、自動 migration をあえて導入しない決定・手動適用手順の記録
- [リファクタリング計画 (2026-05-25)](refactor-plan.html) — `app/` 配下のPythonコードを構造/品質/設計の3軸で機械的にスキャンし、優先度付き改善ロードマップを提示
- [improve-loop 2026-06-09 findings](improve-loop-2026-06-09-findings.md) — `improve-loop -n 10` セッションの完了 PR 一覧と未対応の改善候補（refactor 中規模 2 件 / test 大規模 5 件）の引き継ぎメモ
