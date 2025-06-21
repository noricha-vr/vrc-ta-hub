# VRC技術学術ハブ ドキュメント

VRC技術学術ハブの開発・運用に関するドキュメントです。

## 目次

### システム概要
- [プロジェクト概要](overview.md)
- [アーキテクチャ](architecture.md)
- [データベース設計](database.md)

### 機能別ドキュメント
- [イベント管理システム](event_management.md)
- [イベント定期登録とGoogleカレンダー連携](event_recurrence_system.md)
- [定期イベント管理システム](recurring_events_management.md) ⭐️ New
- [Googleカレンダー同期システム](google_calendar_sync.md)
- [Googleカレンダー同期問題の解決](google_calendar_sync_issue_resolved.md) ⭐️ New
- [AI自動生成機能](ai_generation.md)
- [コミュニティ管理](community_management.md)
- [API仕様](api_specification.md)

### Discord Bot関連
- [Discord Bot仕様書](discord-bot-specification.md)
- [Discord Bot実装ガイド](discord-bot-implementation-guide.md)

### 分析レポート
- [コミュニティスケジュール分析（2025年6月）](community_schedule_analysis_2025_06.md)

### 開発ガイド
- [開発環境セットアップ](development_setup.md)
- [コーディング規約](coding_standards.md)
- [テスト戦略](testing_strategy.md)

### 運用ガイド
- [デプロイ手順](deployment.md)
- [環境変数設定](environment_variables.md)
- [メンテナンス作業](maintenance.md)
- [トラブルシューティング](troubleshooting.md)

### 関数リファレンス
- [関数一覧](functions.md) ⭐️ Updated

## クイックスタート

### 開発環境の起動

```bash
# イメージビルドとコンテナ起動
docker compose up -d --build

# データベースマイグレーション
docker compose exec vrc-ta-hub python manage.py migrate

# スーパーユーザー作成
docker compose exec vrc-ta-hub python manage.py createsuperuser
```

### 基本的な運用コマンド

```bash
# 定期イベントの生成（デフォルト1ヶ月分）
docker compose exec vrc-ta-hub python manage.py generate_recurring_events

# カスタムルールのイベント生成
docker compose exec vrc-ta-hub python scripts/generate_custom_events.py

# DBからGoogleカレンダーへの同期
docker compose exec vrc-ta-hub python scripts/sync_db_to_calendar.py

# 手動カレンダー更新（要REQUEST_TOKEN）
curl -X GET -H "Request-Token: YOUR_REQUEST_TOKEN" https://vrc-ta-hub.com/event/sync/

# LLMイベント自動生成（要REQUEST_TOKEN）
curl -X GET -H "Request-Token: YOUR_REQUEST_TOKEN" https://vrc-ta-hub.com/event/generate/

# テスト実行
docker compose exec vrc-ta-hub python manage.py test
```

## プロジェクト構成

```
vrc-ta-hub/
├── app/                    # Djangoアプリケーション
│   ├── ta_hub/            # メインアプリ
│   ├── community/         # 集会管理
│   ├── event/            # イベント管理
│   ├── event_calendar/   # カレンダー表示
│   ├── account/          # ユーザー認証
│   ├── api_v1/           # REST API
│   └── twitter/          # Twitter連携
├── docs/                  # ドキュメント
├── scripts/              # 管理スクリプト
├── static/               # 静的ファイル
├── templates/            # テンプレート
├── docker-compose.yml    # Docker設定
├── Dockerfile           # Dockerイメージ定義
└── requirements.txt     # Python依存関係
```

## 連絡先

- GitHub: [vrc-ta-hub](https://github.com/noricha-vr/vrc-ta-hub)
- 開発者: @noricha-vr