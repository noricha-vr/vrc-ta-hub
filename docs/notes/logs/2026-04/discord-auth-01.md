## allauth テンプレートの view テストが SocialApp なしで失敗
- 日付: 2026-04-05
- 関連: #181, #182
- 状況: アカウント設定ページの表示修正後、`user_account.tests.test_views` をローカルでまとめて実行した
- 問題: `account/login.html` / `account/register.html` の Discord ログイン導線が `provider_login_url` を呼び、環境変数未設定だと `SocialApp.DoesNotExist` で落ちた
- 対応: ダミーの `DISCORD_CLIENT_ID` と `DISCORD_CLIENT_SECRET` を付けてテストし、`docs/notes/how/discord-auth.md` に再利用パターンを追記した
- → how/discord-auth.md に知識として追記済み
