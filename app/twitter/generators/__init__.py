"""X 自動告知ポスト生成のサブパッケージ。

機能別にモジュール分割:
- common: 共通定数・バリデーション・サニタイズ・整形ユーティリティ
- retry: 文字数バリデーション付きリトライラッパー
- lt_tweet: 発表 (LT) / 特別回告知
- daily_reminder: 当日リマインダー
- event_intro: スライド・記事共有 (過去発表資料の紹介)
- community: 新規集会告知

旧 `twitter.tweet_generator` 経由の import も互換シム経由で動作する。
新規コードは `from twitter.generators import ...` を使うこと。
"""
