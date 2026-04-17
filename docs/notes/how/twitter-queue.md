# Twitter Queue 仕様

現状コードベースの X 自動投稿仕様を、キュー中心で整理したメモ。

## 基本方針

| 項目 | 現在の仕様 |
| --- | --- |
| 正本 | `tweet_queue` を正本として扱う |
| キュー作成責務 | 保存時の signal が作る |
| スケジューラの責務 | 既存キューの再生成・投稿だけを行う |
| スケジューラ実行間隔 | 30分ごと |
| `daily_reminder` の補完作成 | スケジューラではしない |
| 当日重複防止 | 当日の `lt` / `special` は `skipped` にして `daily_reminder` に統合する |

## キュー種別

| `tweet_type` | 意味 | 主な作成トリガー | 紐づき |
| --- | --- | --- | --- |
| `new_community` | 新規集会告知 | `Community` が `approved` になったとき | `community`, 初回 future `event` |
| `lt` | LT 個別告知 | future の承認済み LT 保存時 | `community`, `event`, `event_detail` |
| `special` | 特別回個別告知 | future の承認済み SPECIAL 保存時 | `community`, `event`, `event_detail` |
| `daily_reminder` | 当日告知まとめ | 当日の承認済み LT / SPECIAL 保存・更新・削除時に同期 | `community`, `event` |
| `slide_share` | 資料共有告知 | 過去イベントの承認済み発表に初回資料追加 | `community`, `event`, `event_detail` |

## 保存時の挙動

| 対象 | 条件 | キュー操作 | 補足 |
| --- | --- | --- | --- |
| `Community` | `pending -> approved` | `new_community` を作成 | 既存があれば重複作成しない |
| `EventDetail(LT/SPECIAL)` | `status != approved` | 個別告知は作らない | 当日イベントなら `daily_reminder` の再同期判定だけ走る |
| `EventDetail(LT/SPECIAL)` | 過去イベント | 個別告知は作らない | 当日イベントではないので `daily_reminder` も作らない |
| `EventDetail(LT/SPECIAL)` | future イベントで新規 approved | `lt` または `special` を `generating` で作成 | 非同期生成を開始 |
| `EventDetail(LT/SPECIAL)` | future イベントで approved のまま `speaker` / `theme` 変更 | 未投稿の個別告知を消して再作成 | `start_time` 変更だけでは個別告知を再作成しない |
| `EventDetail(LT/SPECIAL)` | 当日イベントで新規 approved | 個別告知を `skipped` で作成し、`daily_reminder` を同期 | 当日重複防止 |
| `EventDetail(LT/SPECIAL)` | 当日イベントで対象項目更新 | 既存 `daily_reminder` を再生成 | 同一イベントの `daily_reminder` は1件を使い回す |
| `EventDetail(LT/SPECIAL)` | 当日イベントで approved 発表が0件になった | `daily_reminder` を `skipped` に更新 | 削除ではなく残す |
| `EventDetail(LT/SPECIAL)` | 当日イベントで削除 | `daily_reminder` を再同期 | approved 発表が残っていれば再生成、なければ `skipped` |

## `daily_reminder` を再同期する更新項目

| 項目 | 再同期対象か |
| --- | --- |
| 新規作成 | はい |
| `status` | はい |
| `speaker` | はい |
| `theme` | はい |
| `start_time` | はい |
| `detail_type` | はい |
| `event_id` | はい |
| それ以外の項目 | いいえ |

## スケジューラの挙動

| フェーズ | 対象 | 動作 |
| --- | --- | --- |
| Phase 0 | `scheduled_at + 24h` を過ぎた未投稿キュー | `skipped` にする |
| Phase 1 | `generation_failed` / 1時間以上停滞した `generating` | 予約時刻に関係なく同期で再生成する |
| Phase 2 | `ready` かつ `scheduled_at <= now` | X API に投稿する |
| 例外 | 当日の `lt` / `special` | 投稿せず `skipped` にする |
| 例外 | 過去日の `lt` / `special` | 投稿せず `failed` にする |
| 例外 | 当日以外の `daily_reminder` | 投稿せず `failed` にする |
| 非対応 | missing な `daily_reminder` | 自動作成しない |

## 状態の意味

| `status` | 意味 | 主な到達経路 |
| --- | --- | --- |
| `generating` | 生成待ち / 生成中 | signal 直後に作成 |
| `generation_failed` | テキスト生成失敗 | 生成関数が `None` または例外 |
| `ready` | 投稿待ち | `_retry_generation()` 成功 |
| `posted` | 投稿済み | `post_tweet()` 成功 |
| `skipped` | 意図的に投稿対象外 | 当日個別告知、approved 発表なしの `daily_reminder` |
| `failed` | 投稿処理失敗 | X API 失敗、期限切れ、当日外 `daily_reminder` |

## 重複防止と一意制約

| 対象 | 防止方法 |
| --- | --- |
| 同一イベントの `daily_reminder` 多重作成 | DB 一意制約 `unique_daily_reminder_per_event` |
| 当日の個別告知と当日告知の二重投稿 | `lt` / `special` を `skipped` にして `daily_reminder` に統合 |
| future イベントの個別告知重複 | 既存キューの存在確認、または未投稿キュー削除後に再作成 |
| `new_community` 重複 | 既存キュー存在確認 |
| `slide_share` 重複 | 既存キュー存在確認 |

## 実装上の注意

| 項目 | 現在の仕様 |
| --- | --- |
| `daily_reminder` 対象 | `approved` な `LT` / `SPECIAL` のみ |
| `daily_reminder` の本文 | approved 発表を `start_time` 順で最大3件まで載せる |
| `daily_reminder` の発表数0件 | 生成せず `skipped` に落とす |
| `scheduled_at` 入力 | 詳細画面では 30 分刻み（00 / 30）だけ許可 |
| デフォルト予約 | 通常は 19:00 JST |
| 一覧の初期ソート | `scheduled_at desc` |
| 画像 | 生成成功時、`community.poster_image` 由来の URL を補完 |
| 手動投稿 | キュー詳細画面から個別実行可能 |

## 主な参照コード

| 内容 | ファイル |
| --- | --- |
| キューモデル / 一意制約 | `app/twitter/models.py` |
| 保存時 signal | `app/twitter/signals.py` |
| 19:00 バッチ / 再生成 / 投稿 | `app/twitter/views.py` |
| `daily_reminder` 本文生成 | `app/twitter/tweet_generator.py` |
| 回帰テスト | `app/twitter/tests/test_auto_tweet.py` |
