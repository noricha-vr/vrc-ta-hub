# 外部サービスの初期設定

新しく開発に参加する人向けに、このリポジトリで必要な外部サービスの認証情報取得手順をまとめる。
外部サービス側の画面名と導線は 2026-04-06 時点の公式ドキュメントに合わせている。

## どの環境変数に対応しているか

| サービス | 用途 | 必須の環境変数 | 補足 |
|----------|------|----------------|------|
| Google Calendar API | イベントの同期 | `GOOGLE_CALENDAR_ID`, `GOOGLE_CALENDAR_CREDENTIALS`, `REQUEST_TOKEN` | `GOOGLE_CALENDAR_CREDENTIALS` の既定値は `/app/credentials.json` |
| Gemini API | 記事生成・要約 | `GEMINI_API_KEY` | `GEMINI_MODEL` は未指定なら既定値を使う |
| Discord OAuth | ログイン・アカウント連携 | `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` | `SocialApp` は使わず環境変数で設定する |

`.env.local` は Git 管理されず、`app/credentials.json` も `.gitignore` 済み。

## Google Calendar API

このアプリはローカル開発時にサービスアカウント JSON を使って Google Calendar API を呼ぶ。

1. Google Cloud でこの開発用のプロジェクトを作成または選択する。
2. API ライブラリで `Google Calendar API` を有効化する。
3. `IAM と管理` → `サービス アカウント` で専用のサービスアカウントを作成し、JSON キーを発行する。
4. ダウンロードした JSON を `app/credentials.json` に置く。
   既定値以外の場所に置くなら `.env.local` に `GOOGLE_CALENDAR_CREDENTIALS=/app/credentials.local.json` のように指定する。
5. 連携先の Google カレンダーを開き、`カレンダーの設定` → `特定のユーザーとの共有` からサービスアカウントのメールアドレスを追加する。
   権限はイベントを同期できる `予定の変更` 相当を付与する。
6. カレンダー ID を取得して `.env.local` の `GOOGLE_CALENDAR_ID=` に設定する。
   既存の開発用カレンダーを使う場合は `CLAUDE.md` の値を参照してよい。
7. `.env.local` に `REQUEST_TOKEN=` を設定する。
   これは `/event/sync/` などの保護されたエンドポイント呼び出しで使う共有トークン。

### Google Calendar API の確認ポイント

- コンテナ内の既定パスは `/app/credentials.json`。`docker-compose.yaml` ではホストの `app/` ディレクトリが `/app` にマウントされる。
- 外部 API テストを回すときは `RUN_EXTERNAL_API_TESTS=1` に加えて、`GOOGLE_CALENDAR_CREDENTIALS` の実ファイルが必要。

## Gemini API

このリポジトリでは Gemini 用に `GEMINI_API_KEY` を使う。

1. Google AI Studio にログインする。
2. `Get API key` から API キーを作成する。
3. 取得したキーを `.env.local` の `GEMINI_API_KEY=` に設定する。
4. 必要なら `.env.local` の `GEMINI_MODEL=` を上書きする。
   省略時は `google/gemini-2.5-flash-lite-preview-06-17` を使う。

## Discord OAuth

このリポジトリの Discord ログインは django-allauth を使い、`/accounts/discord/login/callback/` をコールバック URL として使う。

1. Discord Developer Portal でアプリケーションを新規作成する。
2. `OAuth2` 設定で Redirects に次の URL を追加する。
   - ローカル: `http://localhost:8015/accounts/discord/login/callback/`
   - 本番: `https://{本番ドメイン}/accounts/discord/login/callback/`
3. アプリケーションの `Client ID` と `Client Secret` を取得する。
4. `.env.local` に以下を設定する。

```dotenv
DISCORD_CLIENT_ID=...
DISCORD_CLIENT_SECRET=...
ACCOUNT_DEFAULT_HTTP_PROTOCOL=http
```

ローカルでは `ACCOUNT_DEFAULT_HTTP_PROTOCOL=http` を設定しておく。
本番は未指定のままで `https` が使われる。

### Discord OAuth の確認ポイント

- このプロジェクトは `SOCIALACCOUNT_PROVIDERS['discord']['APPS']` を環境変数から組み立てるので、Django 管理画面で `SocialApp` を作らない。
- アプリ側の要求スコープは `identify`, `email`。

## 設定後にやること

1. `.env.local` を更新する。
2. 必要なら `app/credentials.json` を配置する。
3. `docker compose up -d --build` でコンテナを起動する。
4. 外部サービス連携を使う処理を確認する。
   - Google Calendar 同期: `docker compose exec vrc-ta-hub python manage.py sync_calendar`
   - Django テスト: `docker compose exec vrc-ta-hub python manage.py test`

## 参考リンク

- Google Calendar API: https://developers.google.com/workspace/calendar/api/guides/overview
- Gemini API キー: https://ai.google.dev/gemini-api/docs/api-key
- Discord OAuth2: https://docs.discord.com/developers/topics/oauth2
