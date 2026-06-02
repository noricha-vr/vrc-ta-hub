# GA4 アクセス解析設定

VRC 技術学術 Hub のアクセス解析同期で必要な GA4 側の設定をまとめる。

## 前提

- GA4 Data API のプロパティ ID は `GA4_PROPERTY_ID` に設定する。
- アプリは集会一覧のポスタークリック時に `poster_click` イベントを送る。
- `poster_click` には集会 ID として `community_id` イベントパラメータを付ける。

## カスタムディメンション

ポスタークリックを集会別に集計するには、GA4 管理画面で次のカスタムディメンションを登録する。

| 項目 | 値 |
|---|---|
| ディメンション名 | `community_id` |
| スコープ | イベント |
| イベント パラメータ | `community_id` |
| 説明 | `poster_click` を集会別に集計するための集会 ID |

登録後、GA4 Data API では `customEvent:community_id` として参照する。

## 設定手順

1. GA4 の対象プロパティを開く。
2. 「管理」>「データの表示」>「カスタム定義」を開く。
3. 「カスタム ディメンションを作成」を選ぶ。
4. 上記の表どおりに `community_id` をイベントスコープで登録する。
5. `poster_click` イベントが発火した後、反映まで待ってから同期を実行する。

## 動作確認

同期確認は対象日の翌日以降に実行する。GA4 の反映には遅延があるため、当日データだけで失敗判断しない。

```bash
docker compose exec vrc-ta-hub python manage.py sync_analytics --date YYYY-MM-DD
```

`customEvent:community_id` が未登録の場合、GA4 Data API は `Field customEvent:community_id is not a valid dimension.` を返す。この場合はアプリ側で握り潰さず、GA4 の設定漏れとして扱う。
