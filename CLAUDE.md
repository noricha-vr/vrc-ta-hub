# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## プロジェクト概要

VRC技術学術ハブ - VRChat内で開催される技術・学術系イベントの情報を集約するWebサイト
- Django 5.2 (LTS) / Python 3.12ベースのWebアプリケーション
- Docker環境で動作（ポート8015）
- Google Cloud Platformにデプロイ

## 開発コマンド

### Docker環境の基本操作
```bash
# イメージビルドとコンテナ起動
docker compose up -d --build

# データベースマイグレーション
docker compose exec vrc-ta-hub python manage.py migrate

# マイグレーションファイル作成＆適用
docker exec -it vrc-ta-hub bash -c "python manage.py makemigrations && python manage.py migrate"

# スーパーユーザー作成
docker compose exec vrc-ta-hub python manage.py createsuperuser

# テスト実行
docker compose exec vrc-ta-hub python manage.py test

# 特定のテストを実行
docker compose exec vrc-ta-hub python manage.py test event.tests.test_generate_blog
```

### カレンダー同期
```bash
# 手動更新（要REQUEST_TOKEN）
curl -X GET -H "Request-Token: YOUR_REQUEST_TOKEN" https://vrc-ta-hub.com/event/sync/

# 定期イベント生成（1ヶ月先まで）
docker compose exec vrc-ta-hub python manage.py generate_recurring_events

# カスタムルールのイベント生成
docker compose exec vrc-ta-hub python scripts/generate_custom_events.py
```

## アーキテクチャ概要

### アプリケーション構成
- `ta_hub`: メインアプリケーション（ランディングページ、共通機能）
- `community`: 集会（コミュニティ）管理機能
- `event`: イベント管理とAI自動コンテンツ生成
- `event_calendar`: カレンダー表示機能
- `account`: ユーザー認証・管理
- `api_v1`: REST API（DRF使用）
- `twitter`: Twitter連携機能
- `sitemap`: SEO対策（サイトマップ生成）

### 外部API連携
- Google Calendar API: イベントカレンダー同期
- Google Gemini API: コンテンツ自動生成
- OpenRouter API: AI処理のバックアップ
- YouTube Data API: 動画情報取得・文字起こし

### データベース構造
1. **Community（集会）**: イベント主催団体の情報
2. **Event（イベント）**: 各回の開催情報（Communityに紐づく）
3. **EventDetail（イベント詳細）**: 発表などの詳細情報（Eventに紐づく）

### 自動コンテンツ生成機能
- YouTube動画の文字起こしからブログ記事を自動生成
- PDFスライドの内容を解析してイベント要約を作成
- SEO最適化のためのメタディスクリプション自動生成

### JSONパーシングの堅牢性
外部APIの不正なJSONに対する複数のフォールバック機構を実装:
- 正規表現による正規化
- 不正なエスケープシーケンスの処理
- 制御文字の除去
- 段階的なクリーニング処理

## テストユーザー（開発用）

ログインが必要な動作確認時は `.env.local` の `TEST_USER_NAME` と `TEST_USER_PASSWORD` を参照。

### AIエージェント向けログインスキップ

AIエージェントがログイン後ページを確認する場合は、毎回手動ログインせずに `DEBUG_LOGIN_SKIP=true` を使える。
この機能は `DEBUG=True` の時だけ有効で、未ログインリクエストに staff 権限のデバッグユーザーを割り当てる。
`DEBUG=False` では `DEBUG_LOGIN_SKIP=true` を設定していても無効。

```bash
docker compose run --rm --service-ports -e DEBUG_LOGIN_SKIP=true vrc-ta-hub \
  /bin/sh -c "python manage.py wait_for_db --timeout 90 && python manage.py runserver 0.0.0.0:8080"
```

デフォルトユーザーは `DEBUG_LOGIN_SKIP_USER_NAME=ai_agent` / `DEBUG_LOGIN_SKIP_USER_EMAIL=ai-agent@example.local`。

## 環境変数（.env.local）
- `SECRET_KEY`: Djangoシークレットキー
- `DEBUG`: デバッグモード設定
- `GOOGLE_API_KEY`: Google Calendar API キー
- `GOOGLE_CALENDAR_ID`: 同期先の Google Calendar ID
- `GEMINI_API_KEY`: Gemini API キー（コンテンツ自動生成）
- `GEMINI_MODEL`: 使用するGeminiモデル（デフォルト: google/gemini-2.5-flash-lite-preview-06-17）
- `OPENROUTER_API_KEY`: OpenRouter API キー（AI バックアップ）
- `REQUEST_TOKEN`: バッチ処理認証用トークン
- `DISCORD_CLIENT_ID`: Discord OAuth 用クライアント ID
- `DISCORD_CLIENT_SECRET`: Discord OAuth 用シークレット

### 外部サービスの初期設定

各 API キーの取得手順は [docs/setup.md](docs/setup.md) を参照。

| サービス | 設定する環境変数 | 取得先 |
|----------|-----------------|--------|
| Google Calendar API | `GOOGLE_API_KEY`, `GOOGLE_CALENDAR_ID` | [GCP Console](https://console.cloud.google.com/) |
| Gemini API | `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/) |
| OpenRouter API | `OPENROUTER_API_KEY` | [OpenRouter](https://openrouter.ai/) |
| Discord OAuth | `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` | [Discord Developer Portal](https://discord.com/developers/applications) |

### Discord OAuth設定
- 環境変数で設定するため、DBのSocialAppは使用しない
- settings.pyで`SOCIALACCOUNT_PROVIDERS`に環境変数から読み込み

## 開発時の注意点
- 外部ネットワーク `my_network` を使用
- Supervisorでプロセス管理
- **デプロイ**: `main` への push で Cloud Build トリガー（`asia-northeast1`）が発火し、以下を自動実行する
  - 本番 (`vrc-ta-hub` / `cloudbuild.yaml`): イメージビルド + Cloud Run リビジョン作成まで自動、**トラフィック切替は手動**（`--no-traffic` 指定）。切替は `gcloud run services update-traffic vrc-ta-hub --region=asia-northeast1 --to-revisions=<REV>=100` または skill `deploy-watch` を使う
  - 開発 (`vrc-ta-hub-dev` / `cloudbuild-dev.yaml`): 完全自動（`--no-traffic` なし、`latestRevision` に 100% トラフィック）
- Cloud Build 実行状況: `gcloud builds list --project=vrc-ta-hub --region=asia-northeast1`（**`--region` 必須**。省略すると global を見て空扱いになる）
- Cloud Build トリガー確認: `gcloud builds triggers list --project=vrc-ta-hub --region=asia-northeast1`
- テストは実際のAPIを使用するため環境変数の設定が必須
- **テストファイルは各Djangoアプリの`tests`ディレクトリ内に配置すること**（例: `app/event/tests/`、`app/community/tests/`）
- プロジェクトルートや`app/`直下にテストファイルを配置しない

## 用語・表記規約
- ユーザー向けの文言・ドキュメントは「LT・ライトニングトーク」ではなく「発表」と表記する（集会の発表は15〜30分が多く、5分前後のLTとは実態が異なるため）
- 内部識別子（DB値 `'LT'`、URLパス、関数・変数名）、`LTS`、Bot の検出キーワード、用語解説文脈は対象外

# Google Calendar ID
本番環境
GOOGLE_CALENDAR_ID=fbd1334d10a177831a23dfd723199ab4d02036ae31cbc04d6fc33f08ad93a3e7@group.calendar.google.com

開発環境
GOOGLE_CALENDAR_ID=d80eac7bdea1505cd9bc16153047c261be94e78607896c5ca567f8cfa78f0be1@group.calendar.google.com

## セッション開始時

セッション開始時は `/note` を実行し、過去の失敗パターンを確認する。

## ドキュメント参照

プロジェクトの全体像やアーキテクチャを把握する際は、DeepWikiを参照する。

**DeepWiki**: https://deepwiki.com/noricha-vr/vrc-ta-hub

### 注意事項

- DeepWikiは**週1回の自動更新**のため、最新のコミットが反映されていない場合がある
- 最終インデックス日時はDeepWikiページ上部で確認可能
- 直近の変更については `git log` やソースコードを直接確認すること
- DeepWikiの情報と実際のコードに差異がある場合は、**実際のコードを優先**する

## Review guidelines

- すべてのレビューコメントは日本語で記述してください。
- コードの問題点、改善提案、賞賛はすべて日本語で行ってください。
