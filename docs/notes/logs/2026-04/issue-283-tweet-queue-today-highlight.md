# Issue 283: ポスト予約一覧の当日行ハイライト調査

## 要件

- ポスト予約一覧で、予約日時が JST 基準の今日に該当する行を薄い黄色で強調する。
- 時分秒ではなく年月日だけで比較する。
- サーバー timezone が UTC でも JST に変換してから判定する。

## 調査結果

- 対象画面は `twitter:tweet_queue_list` で、実装は `app/twitter/views.py` の `TweetQueueListView` と `app/twitter/templates/twitter/tweet_queue_list.html`。
- 一覧は `TweetQueue.scheduled_at` を表示しており、既存の並び替え・ステータス絞り込みは view の context で管理されている。
- テンプレートだけで JST 固定の日付比較を行うと timezone 設定に依存しやすいため、view 側で `ZoneInfo("Asia/Tokyo")` を使って判定結果を渡すのが最小影響。

## 改善案と実装方針

- `TweetQueueListView.get_context_data()` で現在ページの `TweetQueue` を走査し、JST 基準の今日に該当する ID セットを `today_tweet_queue_ids` として渡す。
- テンプレートは `item.pk in today_tweet_queue_ids` の場合のみ、Bootstrap の `table-warning` を行に付与する。
- 境界値は UTC の aware datetime を使い、JST で昨日 23:59、今日 00:00、明日 00:00 をテストする。

## 検証手順

- `docker compose -f docker-compose.yaml exec -T vrc-ta-hub python manage.py test twitter.tests.test_tweet_queue_views.TweetQueueListViewTest`
- `docker compose -f docker-compose.yaml exec -T vrc-ta-hub python manage.py check`
- `git diff --check`
