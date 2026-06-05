# 調査・分析レポート

機械的スキャンや手動調査の結果をまとめた、再利用可能な分析レポートの入口です。

## レポート一覧

- [Issue #399 スタッフ削除確認の表示名エスケープ](issue-399-member-delete-confirm-xss.md) — `display_name` を JavaScript 文字列コンテキストで安全に扱うための調査と検証方針
- [Issue #356 OpenRouter HTTP-Referer 調査](issue-356-openrouter-http-referer.md) — preview/staging URL が OpenRouter の `HTTP-Referer` に漏れないようにする調査と検証方針
- [Issue #343 LT申請「追加情報」テンプレート初期値化](issue-343-lt-application-additional-info-initial.md) — LT申請フォームのテンプレート表示を placeholder から initial に変更する調査と検証方針
- [リファクタリング計画 (2026-05-25)](refactor-plan.html) — `app/` 配下のPythonコードを構造/品質/設計の3軸で機械的にスキャンし、優先度付き改善ロードマップを提示
