# VRChat Technology and Academic Hub (vrc-ta-hub) - プロジェクトコンテキスト

## プロジェクト概要
**vrc-ta-hub** は、VRChat内で開催される技術・学術系イベントの情報を集約・表示するためのDjangoベースのWebアプリケーションです。ユーザーがイベントを見つけたり、Googleカレンダー連携でスケジュールを確認したり、ライトニングトーク（LT）などの過去のアーカイブにアクセスしたりするためのハブとして機能します。

## 技術スタック
*   **バックエンド:** Python 3.9+, Django 4.2
*   **API:** Django REST Framework (DRF), DRF Spectacular (OpenAPI/Swagger)
*   **データベース:** MySQL (本番環境), SQLite (開発環境)
*   **フロントエンド:** Django Templates, Bootstrap 5.3
*   **インフラ:** Docker, Docker Compose, Cloud Run (`cloudbuild.yaml` から推測)
*   **ストレージ:** Cloudflare R2 (S3互換) via `django-storages` (本番環境)
*   **外部API:**
    *   Google Calendar API (スケジュール管理)
    *   YouTube Data API (動画コンテンツ)
    *   LLMs: Google Gemini, OpenRouter (コンテンツ生成/要約)
    *   Twitter/X (ソーシャルメディア連携)

## ディレクトリ構成
*   `app/` - メインアプリケーションのソースコード
    *   `manage.py` - Django管理スクリプト
    *   `website/` - プロジェクト設定 (`settings.py`, `urls.py`, `wsgi.py`)
    *   `ta_hub/` - コアアプリケーション (トップページなど)
    *   `event/` - イベント管理ロジック、カレンダー同期、LLM生成
    *   `community/` - コミュニティ/集会管理
    *   `api_v1/` - REST API実装
    *   `account/` - ユーザー認証・管理
    *   `static/` - 静的アセット (CSS, JS, 画像)
    *   `templates/` - HTMLテンプレート
*   `docs/` - プロジェクトドキュメント
*   `scripts/` - ユーティリティスクリプト (イベント同期など)
*   `docker-compose.yaml` - ローカル開発用コンテナ定義

## 開発ワークフロー

### 1. 環境セットアップ
このプロジェクトはローカル開発に `docker-compose` を使用します。
*   **環境変数ファイル:**
    *   `.env`: 本番用/共有シークレット (CI/CDや本番環境で使用)
    *   `.env.local`: ローカル開発用の上書き設定 (`docker-compose.yaml` で使用)
*   **ネットワーク:** `my_network` という外部ネットワークが必要です。
    *   存在しない場合: `docker network create my_network`

### 2. アプリケーションの起動
```bash
docker compose up -d --build
```
`http://localhost:8015` でアクセスできます (ホストポート 8015 がコンテナの 8080 にマップされています)。

### 3. データベースマイグレーション
コンテナ内でマイグレーションを実行します:
```bash
docker compose exec vrc-ta-hub python manage.py migrate
```

### 4. 静的ファイル (Static Files)
*   **開発環境 (`DEBUG=True`):** ローカルから配信されます。
*   **本番環境 (`DEBUG=False`):** Cloudflare R2 に同期されます。
*   **手動同期:** `docs/static_files_sync.md` を参照してください。
    *   `docker-compose.yaml` で `DEBUG=False` に設定し、`.env` を読み込む必要があります。

### 5. テスト
Djangoテストを実行します:
```bash
docker compose exec vrc-ta-hub python manage.py test
```

## 主要な設定 (`app/website/settings.py`)
*   **Static/Media:** `DEBUG` フラグに基づいて、ローカルストレージと S3/R2 を切り替えます。
*   **APIs:** Google (Calendar, YouTube), Gemini, OpenRouter, AWS (R2/SES用) のキーが必要です。
*   **Logging:** コンソールおよびファイル (`logs/django.log` ※デバッグ時) に出力するように設定されています。

## よく使うコマンド
*   **ビルド:** `docker compose build`
*   **マイグレーション作成:** `docker compose exec vrc-ta-hub python manage.py makemigrations`
*   **スーパーユーザー作成:** `docker compose exec vrc-ta-hub python manage.py createsuperuser`
*   **静的ファイル収集:** `docker compose run --rm vrc-ta-hub python manage.py collectstatic --noinput` (環境変数の設定を確認してから実行してください)

## ドキュメント
詳細は `docs/` ディレクトリを参照してください:
*   `docs/index.md`: ドキュメントハブ
*   `docs/api_specification.md`: API詳細
*   `docs/static_files_sync.md`: 静的アセットのR2同期ガイド