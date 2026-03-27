# セキュリティ監査対応 (PR #125, Issue #123)

日付: 2026-03-27

## 概要

セキュリティ監査で CRITICAL 1件 / HIGH 4件の脆弱性を検出。4件を修正、2件はリスク受容。

## 修正内容

### CRITICAL: CWE-79 Stored XSS (twitter/views.py)
- LLM生成テキストを `html.escape()` でサニタイズしてから `<br>` 変換
- テンプレートの `|safe` は維持（エスケープ済みHTMLのため必要）
- 重要: `escape → replace` の順序で処理すること（逆にすると `<br>` もエスケープされる）

### HIGH: CWE-614 セキュリティヘッダー (settings.py)
- `if not DEBUG:` で本番のみ SESSION_COOKIE_SECURE, CSRF_COOKIE_SECURE, HSTS を有効化
- `SECURE_SSL_REDIRECT` は Cloud Run + nginx 構成のため追加しない（リダイレクトループ回避）

### HIGH: CWE-532 print文 (settings.py)
- `_mask()` ヘルパーで先頭5文字+`***` にマスク化
- settings.py 段階では LOGGING 未初期化のため logger 置換は不適切

### HIGH: CWE-209 エラーレスポンス (event/views.py, views_llm_generate.py)
- `str(e)` を固定メッセージに変更
- 詳細は既存の logger.error で記録済み

## リスク受容（対応不要と判断）

- **cookies.txt**: `.gitignore` 済み、Git未追跡
- **CORS_ALLOW_ALL_ORIGINS**: `CORS_URLS_REGEX` で `/api/` パスのみ制限済み。公開API

## 技術的負債（backlog）

- `structured_data_json|safe` の JSON-in-script 出力を Django `json_script` タグに移行すべき
