# VRC 技術学術ハブ（VRChat Technology and Academic Hub）

このプロジェクトはVRChatで開催されている技術学術系イベントを知って、参加してもらい、盛り上げていくためのWeb制作プロジェクトです。

![VRChat技術学術ハブ Webサイト](app/ta_hub/static/ta_hub/image/screenshot.png)

https://vrc-ta-hub.com

## 概要

VRChat技術学術ハブは、VRChat内で開催される技術・学術系イベントの情報を集約し、利用者が簡単にイベントの情報にアクセスできるようにするためのWebサイトです。

## 主な機能

1. **ホームページ（ランディングページ）:**
    - プロジェクトの概要と主要な機能について説明します。

2. **開催日程ページ:**
    - GoogleカレンダーをAPIで埋め込み、今後開催予定のイベントをカレンダー形式で表示します。
    - 各イベントの詳細情報へのリンクを提供します。

3. **集会一覧ページ:**
    - これまでに開催された全てのイベントをリスト形式で表示します。
    - 検索（フィルター）機能を実装し、ユーザーが関心のあるイベントを見つけやすくします。
    - 各イベントの詳細ページへのリンクを提供します。

4. **集会詳細ページ:**
    - 各イベントの詳細情報を提供します。
    - イベントの概要、開催日時、登壇者、関連資料などの情報を掲載します。
    - 各回のイベントで行われたライトニングトークの資料やYouTube動画を埋め込みます。

5. **LTアーカイブページ:**
    - 過去に開催されたライトニングトークの資料やYouTube動画を一覧で表示します。
    - 各資料や動画へのリンクを提供します。

6. **ユーザーアカウント:**
    - イベント主催者はアカウントを作成し、集会情報とイベント情報を登録・管理できます。
    - 承認機能により、新規登録された集会は管理者または既存の承認済み集会運営者によって承認されるまで公開されません。
    - 設定画面では、ユーザーは自身のプロフィール情報、集会情報、パスワードなどを変更できます。

7. **自動コンテンツ生成機能:**
    - Google Gemini APIとOpenRouter APIを使用して、イベント情報からブログ記事を自動生成します。
    - YouTube動画の文字起こしとPDFスライドの内容を分析し、イベント内容を要約します。
    - メタディスクリプションやタイトルを自動生成し、SEO最適化を支援します。

8. **API (api/v1):**
    - WebサイトのデータはRESTful APIを通して取得できます。
    - APIはJSON形式でデータを提供します。
    - 読み取り専用の公開APIと、認証が必要な管理APIの両方を提供しています。
    - Swagger UIによる対話的なAPIドキュメントを提供しています。

## API エンドポイント

### 公開API（認証不要）

読み取り専用のAPIエンドポイント：

1. **集会情報 API:**
    - エンドポイント: `https://vrc-ta-hub.com/api/v1/community/`
    - 説明: 承認済み集会の情報を取得します。

2. **イベント情報 API:**
    - エンドポイント: `https://vrc-ta-hub.com/api/v1/event/`
    - 説明: 今後開催予定のイベント情報を取得します。

3. **イベント詳細情報 API:**
    - エンドポイント: `https://vrc-ta-hub.com/api/v1/event_detail/`
    - 説明: 公開されているイベント詳細（ライトニングトークなど）の情報を取得します。

### 管理API（認証必要）

イベント詳細の作成・編集・削除が可能なAPIエンドポイント：

- エンドポイント: `https://vrc-ta-hub.com/api/v1/event-details/`
- 認証方法: APIキー認証（Bearer Token）またはセッション認証
- 権限: コミュニティオーナーは自分のイベントのみ、管理者は全イベントを操作可能

#### APIキーの取得方法

1. [アカウント設定](https://vrc-ta-hub.com/account/settings/)にログイン
2. 「API管理」セクションから「APIキー管理」ページへ移動
3. 新規APIキーを作成（キーは一度しか表示されません）

#### リクエスト例

```bash
# イベント詳細の作成
curl -X POST \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "event": 1,
    "detail_type": "LT",
    "speaker": "発表者名",
    "theme": "発表テーマ",
    "start_time": "20:00:00",
    "duration": 30,
    "generate_from_pdf": true
  }' \
  https://vrc-ta-hub.com/api/v1/event-details/
```

### APIドキュメント

- **Swagger UI**: [https://vrc-ta-hub.com/api/docs/](https://vrc-ta-hub.com/api/docs/)
- **ReDoc**: [https://vrc-ta-hub.com/api/redoc/](https://vrc-ta-hub.com/api/redoc/)
- **OpenAPIスキーマ**: [https://vrc-ta-hub.com/api/schema/](https://vrc-ta-hub.com/api/schema/)

Swagger UIでは、APIをブラウザから直接テストすることができます。

## 使用技術

- Django（Python）
- Bootstrap 5.3
- SQLite (開発用)
- MySQL (本番用)
- Google Calendar API
- Google Gemini API
- OpenRouter API
- YouTube Data API
- django_filters
- djangorestframework
- drf-spectacular（OpenAPI/Swagger対応）
- youtube_transcript_api
- openai（OpenRouterとの互換クライアント）
- PyPDF（PDFファイルの解析）
- bleach
- markdown
- pydantic

## 開発環境構築

### 前提条件

- Python 3.9以上
- Docker
- Docker Compose

### 手順

1. **リポジトリのクローン:**

   ```bash
   git clone https://github.com/noricha-vr/vrc-ta-hub.git
   cd vrc-ta-hub
   ```

2. **.envファイルの作成:**

   ```bash
   cp .env.example .env
   ```

   `.env` ファイルを編集し、必要な環境変数を設定します。

   主要な環境変数:
   - `OPENROUTER_API_KEY`: OpenRouter APIのキー（自動コンテンツ生成に必要）
   - `GOOGLE_API_KEY`: Google APIのキー（YouTube動画処理に必要）
   - `GEMINI_MODEL`: 使用するGeminiモデル名（例: `google/gemini-2.0-flash-001`）

3. **Dockerコンテナのビルドと起動:**

   ```bash
   docker compose up -d --build
   ```

4. **データベースのマイグレーション:**

   ```bash
   docker compose exec vrc-ta-hub python manage.py migrate
   ```

5. **スーパーユーザーの作成:**

   ```bash
   docker compose exec vrc-ta-hub python manage.py createsuperuser
   ```

   指示に従って、スーパーユーザーのユーザー名、メールアドレス、パスワードを設定します。

6. **開発サーバーへのアクセス:**

   ブラウザで `http://localhost:8000` にアクセスします。

### テスト環境のセットアップ

本プロジェクトでは実際のAPIを使用したテストを実行します。

1. **テスト用環境変数の設定:**

   `.env`ファイルに以下の環境変数が設定されていることを確認します：
   ```
   OPENROUTER_API_KEY=your_api_key
   GOOGLE_API_KEY=your_api_key
   GEMINI_MODEL=google/gemini-2.0-flash-001
   ```

2. **テストの実行:**

   ```bash
   docker compose exec vrc-ta-hub python manage.py test
   ```

   特定のテストを実行する場合：
   ```bash
   docker compose exec vrc-ta-hub python manage.py test event.tests.test_generate_blog
   ```

   API認証のテストを実行する場合：
   ```bash
   docker compose exec vrc-ta-hub python manage.py test api_v1.tests.test_event_detail_api
   ```

## データモデル

1. **集会モデル:**
    - 集会の基本情報を管理します。
    - フィールド：名称、概要、開催頻度、開催場所、主催者、タグ、対応プラットフォームなど

2. **イベントモデル:**
    - 各回のイベントの情報を管理します。
    - フィールド：集会（外部キー）、開催日時、曜日など
    - 集会モデルとの1対多の関係を持ちます。

3. **イベント詳細モデル:**
    - 各イベントのライトニングトークの詳細情報を管理します。
    - フィールド：イベント（外部キー）、開始時刻、発表時間、発表者、テーマ、資料、YouTube動画のURL、自動生成コンテンツなど
    - イベントモデルとの1対多の関係を持ちます。
    - タイプ（LT、特別企画、ブログ）による分類をサポートします。

4. **APIキーモデル:**
    - API認証用のキーを管理します。
    - ユーザーごとに複数のAPIキーを作成可能です。
    - 最終使用日時の追跡機能を持ちます。

## 実装の特徴

### JSONパーシングの堅牢性向上

外部APIとの連携時に発生するJSONパーシングの問題に対応するため、以下の工夫を施しています：

1. **正規表現を使用したJSON正規化**:
   ```python
   normalized_json = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', json_str)
   ```

2. **不正なエスケープシーケンスの処理**:
   ```python
   clean_json = re.sub(r'\\([^"\\/bfnrtu])', r'\1', json_str)
   ```

3. **複数のフォールバック機構**:
   - 正規化されたJSONでパースを試行
   - 失敗した場合はクリーニングされたJSONで試行
   - さらに失敗した場合は制御文字を削除したバージョンで試行
   - 最終手段として積極的なクリーニングを実施

これらの対策により、外部APIが返す不正確なJSONに対しても堅牢にデータを抽出できます。

### テスト戦略

1. **統合テスト**:
   - 実際のAPIを使用したエンドツーエンドのテスト
   - 環境変数が適切に設定されている場合のみ実行

2. **単体テスト**:
   - 基本的な機能の検証
   - 外部依存性のないコンポーネントのテスト

3. **例外処理に重点**:
   - APIエラーに対する適切な処理
   - フォールバックメカニズムのテスト

## 開発への貢献

本プロジェクトはGitHubで公開されています。開発への貢献を歓迎します！

- [https://github.com/noricha-vr/vrc-ta-hub](https://github.com/noricha-vr/vrc-ta-hub)

コントリビューション方法:

1. リポジトリをフォーク
2. 機能ブランチを作成（`git checkout -b feature/amazing-feature`）
3. 変更をコミット（`git commit -m 'Add some amazing feature'`）
4. ブランチにプッシュ（`git push origin feature/amazing-feature`）
5. プルリクエストを作成

## ライセンス

MITライセンスの下で公開されています。詳細はLICENSEファイルを参照してください。
