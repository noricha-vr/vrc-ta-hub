# 集会活動監視（Grok + X Search）

## 結論

運用時に **Grok Build CLIそのものを常駐実行するのではなく**、xAI Responses API の
`grok-4.5` とサーバー側ツール `x_search` を使用する。

この方式なら X API の契約・OAuthを使わず、X上の投稿検索、日付範囲の限定、構造化JSON出力を
1リクエストで実行できる。Grok Buildはコード実装・調査用のCLI/エージェントであり、定期バッチの
本番依存としてはAPIのほうが小さく安定する。

## 安全設計

1. 過去90日をX Searchで確認する。
2. 判定を `active / inactive / uncertain` のJSONへ固定する。
3. `uncertain`、APIエラー、低信頼度の非活動判定では何もしない。
4. 最初の `inactive` でDiscordへ警告する。
5. 14日以上の猶予と2回以上の連続 `inactive` を満たした場合だけ `Community.end_at` を設定する。
6. 公開一覧・今後のイベント一覧は既に `end_at IS NULL` を条件にしているため、設定直後から非表示になる。
7. DBイベント、定期ルール、Googleカレンダーは自動削除しない。誤判定を戻せるよう、破壊的な既存
   クリーンアップは管理者確認後に行う。
8. Discord警告が正常送信されていない集会は自動非表示にしない。
9. 判定、根拠URL、信頼度、xAIレスポンスID、実費を監査テーブルへ保存する。
10. Cloud Runの多重起動はDBリースで集会単位に抑止する。

## 追加されるもの

- `CommunityActivityState`: 集会ごとの最新状態、自動非表示の進行、監視ON/OFF
- `CommunityActivityCheck`: 1回ごとの判定・根拠・費用の監査履歴
- `check_community_activity`: 手動確認用Django管理コマンド
- `POST /community/activity-monitor/run/`: Cloud Scheduler向け内部エンドポイント
- Django Adminの活動状態・履歴画面

## 本番導入手順

### 1. xAI APIキーをSecret Managerへ登録

`cloudbuild.yaml` は `XAI_API_KEY` シークレットを参照するため、**PRをマージする前に作成する**。

```bash
export XAI_API_KEY='xai-...'

if gcloud secrets describe XAI_API_KEY --project=vrc-ta-hub >/dev/null 2>&1; then
  printf '%s' "$XAI_API_KEY" \
    | gcloud secrets versions add XAI_API_KEY \
        --project=vrc-ta-hub \
        --data-file=-
else
  printf '%s' "$XAI_API_KEY" \
    | gcloud secrets create XAI_API_KEY \
        --project=vrc-ta-hub \
        --replication-policy=automatic \
        --data-file=-
fi
```

Cloud Runの実行サービスアカウントに、既存シークレットと同様に
`roles/secretmanager.secretAccessor` が付いていることも確認する。

### 2. マイグレーション

通常のデプロイフローでマイグレーションが自動実行されない場合は次を実行する。

```bash
python manage.py migrate
```

### 3. まずdry-run

```bash
python manage.py check_community_activity \
  --dry-run \
  --force \
  --limit 10
```

特定の集会だけ確認する場合:

```bash
python manage.py check_community_activity \
  --dry-run \
  --force \
  --community-id 123
```

### 4. 通知だけで運用

初期値は次の通りで、自動非表示は無効。

```env
COMMUNITY_ACTIVITY_AUTO_HIDE=false
COMMUNITY_ACTIVITY_LOOKBACK_DAYS=90
COMMUNITY_ACTIVITY_CHECK_INTERVAL_DAYS=7
COMMUNITY_ACTIVITY_WARNING_DAYS=14
COMMUNITY_ACTIVITY_REQUIRED_INACTIVE_CHECKS=2
COMMUNITY_ACTIVITY_MIN_CONFIDENCE=0.85
COMMUNITY_ACTIVITY_BATCH_SIZE=5
```

最初の1〜2週間は `false` のまま実行し、Django Adminの「集会活動監視状態」「集会活動確認履歴」
で誤判定率を確認する。不定期・季節開催の集会は `monitoring_enabled` をOFFにする。

### 5. Cloud Scheduler

公開Cloud Runサービスの既存運用に合わせて `Request-Token` ヘッダーで認証する。
75件を一度に処理せず、5件ずつ1日4回実行する例:

```bash
gcloud scheduler jobs create http community-activity-monitor \
  --project=vrc-ta-hub \
  --location=asia-northeast1 \
  --schedule='0 2,8,14,20 * * *' \
  --time-zone='Asia/Tokyo' \
  --uri='https://vrc-ta-hub.com/community/activity-monitor/run/?limit=5' \
  --http-method=POST \
  --headers="Request-Token=${REQUEST_TOKEN}"
```

既にジョブがある場合は `create` を `update` に置き換える。長期的には専用の非公開Cloud Run Jobと
OIDC認証へ移すと、共有トークンをCloud Scheduler設定へ保持せずに済む。

### 6. 自動非表示を有効化

判定履歴を確認後、Cloud Runの環境変数を変更する。

```env
COMMUNITY_ACTIVITY_AUTO_HIDE=true
```

有効化後も、1回の判定では非表示にならない。Discord警告成功、2回連続判定、14日の猶予をすべて
満たしたときだけ `end_at` が設定される。

## 戻し方

誤判定時は既存の「再開」操作、またはAdminで `Community.end_at` を空にする。再判定ですぐ戻らないよう、
同時に「集会活動監視状態」の `monitoring_enabled` をOFFにする。

自動非表示では関連イベントやGoogleカレンダーを削除していないため、復旧後に既存データを再生成する
必要はない。実際に終了した集会だと確認できた場合だけ、既存の管理者クリーンアップを実行する。

## 費用の見方

xAIのレスポンスに含まれる `usage.cost_in_usd_ticks` を毎回保存する。1 USDは10,000,000,000 ticks。
Adminの履歴から実費を確認できる。X Searchはモデルのトークン料金とは別にツール呼び出し料金が発生し、
モデルが1リクエスト内で複数回検索した場合もレスポンスのticksへ合算される。

## 制約

- Xアカウントが非公開・削除済み、名称が一般的、開催が季節性の場合は `uncertain` になりやすい。
- 「投稿がない」だけで確実な終了とは言えないため、猶予・連続判定・手動除外は外さない。
- X上に活動を出さない集会は、この仕組みだけでは終了判定できない。将来は主催者への定期確認メール、
  VRChat Groupの更新、Hub内のイベント作成履歴を別シグナルとして加えると精度が上がる。
