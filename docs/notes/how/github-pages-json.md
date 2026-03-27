# VRChatワールド向けJSON配信（toGithubPagesJson）

## 概要

VRChatワールド内カレンダー表示用のJSONデータを、GitHub Pages経由で配信する仕組み。

## データフロー

```
vrc-ta-hub API (/api/v1/community/?format=json)
    ↓ fetch_data.py が取得
toGithubPagesJson リポジトリ (noricha-vr/toGithubPagesJson)
    ↓ convert_data() で日本語化・整形
docs/sample.json
    ↓ GitHub Actions → gh-pages ブランチ
https://noricha-vr.github.io/toGithubPagesJson/sample.json
    ↓ VRChatワールドが読み込み
ワールド内カレンダー表示
```

## 関連リポジトリ

| リポジトリ | 用途 | 公開URL |
|-----------|------|---------|
| noricha-vr/toGithubPagesJson | 人間向けJSON（曜日日本語化・ジャンル判定） | https://noricha-vr.github.io/toGithubPagesJson/sample.json |
| noricha-vr/vrc-ta-hub-json | API直結JSON（community/event/lt） | https://noricha-vr.github.io/vrc-ta-hub-json/ |

## GitHub Actions設定

- スケジュール: 3日ごと UTC 00:00（JST 09:00）
- 手動トリガー: `gh workflow run schedule.yml --repo noricha-vr/toGithubPagesJson`
- ワークフロー: fetch_data.py → test_format.py（検証） → gh-pages デプロイ

## JSONフォーマット（sample.json）

```json
{
  "ジャンル": "技術系|学術系|その他",
  "曜日": "日曜日|月曜日|...|その他",
  "イベント名": "string",
  "開始時刻": "HH:MM",
  "開催周期": "string",
  "主催・副主催": "string",
  "Join先": "URL",
  "Discord": "URL",
  "Twitter": "URL",
  "ハッシュタグ": "string",
  "ポスター": "URL|null",
  "イベント紹介": "string"
}
```

## 変換ロジック

- 曜日: `Mon` → `月曜日`
- ジャンル: `tags` に `tech` → 技術系、`academic` → 学術系
- ポスター: 空文字 → null
- 時刻: `HH:MM:SS` → `HH:MM`
- Join先: `group_url` 優先、なければ `organizer_url`
- ソート: 曜日順 → イベント名順

## APIフィルタ条件（CommunityViewSet）

```python
Community.objects.filter(
    end_at__isnull=True,   # アクティブなコミュニティのみ
    status='approved',      # 承認済みのみ
).exclude(
    tags=[]                # タグ必須（テストデータ混入防止）
)
```

## テストデータ混入パターンと対策

- 問題: `status='approved'` かつ `tags=[]` のテストコミュニティがAPIに漏出
- 対策1: DB修正（status を rejected に変更）
- 対策2: APIフィルタで `.exclude(tags=[])` 追加（再発防止）
- 対策3: JSON再生成（`gh workflow run` で手動トリガー）

## 外部利用

PosterMovieMaker プロジェクトも sample.json を参照している。更新内容は他プロジェクトに波及する点に注意。

## 本番DB直接接続

```bash
# .env.production.local の認証情報を使用（AWS RDS）
export $(grep -E '^DB_' .env.production.local | xargs)
mysql --skip-ssl -h "$DB_HOST" -u "$DB_USER" -p"$DB_PASSWORD" "$DB_NAME"
```

テーブル名は `community`（`community_community` ではない）。
