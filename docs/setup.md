# 外部サービスの初期設定

新規開発者が各外部サービスの認証情報を取得し、`.env.local` に設定するための手順。

## Google Calendar API

イベントカレンダーの同期に使用。

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを開く
2. 「API とサービス」>「ライブラリ」で **Google Calendar API** を有効化
3. 「認証情報」> API キーを作成（またはサービスアカウントを作成して JSON キーをダウンロード）
4. `.env.local` に設定:

```bash
GOOGLE_API_KEY=取得したAPIキー
GOOGLE_CALENDAR_ID=d80eac7bdea1505cd9bc16153047c261be94e78607896c5ca567f8cfa78f0be1@group.calendar.google.com
```

- `GOOGLE_CALENDAR_ID` の開発用 / 本番用の値は `CLAUDE.md` を参照
- サービスアカウントの JSON キーを使う場合は `GOOGLE_CALENDAR_CREDENTIALS` にパスを設定

## Gemini API

AI によるコンテンツ自動生成（ブログ記事・要約・メタディスクリプション）に使用。

1. [Google AI Studio](https://aistudio.google.com/) にアクセス
2. 「Get API key」から API キーを取得
3. `.env.local` に設定:

```bash
GEMINI_API_KEY=取得したAPIキー
GEMINI_MODEL=google/gemini-2.5-flash-lite-preview-06-17
```

- `GEMINI_MODEL` は省略可（デフォルト値あり）

## OpenRouter API

AI 処理のバックアップ（ブログ生成・定期イベント生成・ツイート文生成）に使用。

1. [OpenRouter](https://openrouter.ai/) でアカウント作成
2. 「Keys」から API キーを取得
3. `.env.local` に設定:

```bash
OPENROUTER_API_KEY=取得したAPIキー
```

## Discord OAuth

ユーザーの Discord ログインに使用。

1. [Discord Developer Portal](https://discord.com/developers/applications) でアプリケーションを作成
2. 「OAuth2」タブを開く
3. 「Redirects」にコールバック URL を追加:
   - 開発環境: `http://localhost:8015/accounts/discord/login/callback/`
4. 「Client ID」と「Client Secret」を取得
5. `.env.local` に設定:

```bash
DISCORD_CLIENT_ID=取得したクライアントID
DISCORD_CLIENT_SECRET=取得したクライアントシークレット
ACCOUNT_DEFAULT_HTTP_PROTOCOL=http
```

- `ACCOUNT_DEFAULT_HTTP_PROTOCOL=http` は開発環境のみ（本番はデフォルト `https`）

## Discord Webhook（任意）

管理者通知に使用。開発時は未設定でも動作する。

1. Discord サーバーの「チャンネル設定」>「連携サービス」>「ウェブフック」で作成
2. `.env.local` に設定:

```bash
DISCORD_WEBHOOK_URL=ウェブフックURL
DISCORD_REPORT_WEBHOOK_URL=レポート用ウェブフックURL
```

## X (Twitter) API（任意）

イベント情報の自動ツイートに使用。開発時は未設定でも動作する。

1. [X Developer Portal](https://developer.x.com/) でアプリを作成
2. OAuth 1.0a の API Key / Secret を取得
3. アクセストークンの生成:

```bash
docker compose exec vrc-ta-hub python manage.py generate_x_token
```

4. `.env.local` に設定:

```bash
X_API_KEY=取得したAPIキー
X_API_SECRET=取得したAPIシークレット
X_ACCESS_TOKEN=生成されたアクセストークン
X_ACCESS_TOKEN_SECRET=生成されたアクセストークンシークレット
```

## 最低限必要な環境変数

`SECRET_KEY` のみ設定すれば基本的な開発が可能です。DB・ストレージの接続情報は `.env.example` にデフォルト値が入っています。

| 環境変数 | 必須度 | 用途 |
|----------|--------|------|
| `SECRET_KEY` | 必須 | Django セッション・CSRF |
| `GOOGLE_API_KEY` | 推奨 | カレンダー同期 |
| `GOOGLE_CALENDAR_ID` | 推奨 | 同期先カレンダー |
| `GEMINI_API_KEY` | 推奨 | AI コンテンツ生成 |
| `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` | 推奨 | Discord ログイン |
| `OPENROUTER_API_KEY` | 任意 | AI バックアップ |
| `REQUEST_TOKEN` | 任意 | バッチ処理認証 |
