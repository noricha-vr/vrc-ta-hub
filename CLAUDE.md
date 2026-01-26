# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## プロジェクト概要

VRC技術学術ハブ - VRChat内で開催される技術・学術系イベントの情報を集約するWebサイト
- Django 4.2 / Python 3.12ベースのWebアプリケーション
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
3. **EventDetail（イベント詳細）**: LTなどの詳細情報（Eventに紐づく）

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

## 環境変数（.env.local）
- `OPENROUTER_API_KEY`: OpenRouter APIキー
- `GOOGLE_API_KEY`: Google APIキー
- `GEMINI_MODEL`: 使用するGeminiモデル（例: google/gemini-2.0-flash-001）
- `DEBUG`: デバッグモード設定
- `SECRET_KEY`: Djangoシークレットキー
- `REQUEST_TOKEN`: カレンダー更新用トークン
- `DISCORD_CLIENT_ID`: Discord OAuth用クライアントID
- `DISCORD_CLIENT_SECRET`: Discord OAuth用シークレット

### Discord OAuth設定
- 環境変数で設定するため、DBのSocialAppは使用しない
- settings.pyで`SOCIALACCOUNT_PROVIDERS`に環境変数から読み込み

## 開発時の注意点
- 外部ネットワーク `my_network` を使用
- Supervisorでプロセス管理
- 本番環境はGoogle Cloud Build（cloudbuild.yaml）でデプロイ
- テストは実際のAPIを使用するため環境変数の設定が必須
- **テストファイルは各Djangoアプリの`tests`ディレクトリ内に配置すること**（例: `app/event/tests/`、`app/community/tests/`）
- プロジェクトルートや`app/`直下にテストファイルを配置しない

# Google Calendar ID
本番環境
GOOGLE_CALENDAR_ID=fbd1334d10a177831a23dfd723199ab4d02036ae31cbc04d6fc33f08ad93a3e7@group.calendar.google.com

開発環境
GOOGLE_CALENDAR_ID=d80eac7bdea1505cd9bc16153047c261be94e78607896c5ca567f8cfa78f0be1@group.calendar.google.com
