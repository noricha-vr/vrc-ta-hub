# 定期イベントの削除・日付移動を保持する仕組み

## 背景

定期イベント生成は、ルールに一致する未来の開催回が欠けていると再作成する。
そのため、主催者が開催回を削除したり別日に移動したりしても、次回の生成処理で
元の開催日が復活する問題があった。

## 方針

`EventOccurrenceTombstone` に主催者が取り消した元の開催日を保存し、
`generate_recurring_events` と定期イベント永続化処理の両方で生成候補から除外する。

- 一意性は集会と元開催日の組み合わせで、日単位に再生成を抑止する
- 理由は `deleted` または `rescheduled`
- 定期ルールへの外部キーは持たない
- `--reset-future` やルール削除など、システム都合の削除では作成しない
- Vket運営同期によるEvent作成・日付更新では作成しない

Event本体は `(community, date, start_time)` で一意だが、tombstoneは
`(community, date)` で一意である。このため、同じ集会・同じ日に別の定期系列や
別時刻の開催候補があっても、その日全体が生成抑止対象になる。開催日の削除・移動を
「その日の開催を取り消した」という単純な例外として扱える一方、時刻・系列単位では
例外指定できないトレードオフを許容する。

削除時は、DBを正としてtransaction内でtombstone作成とEvent削除を先に完了する。
親Eventを削除する場合は、CASCADEされる子開催回も削除前に列挙して記録する。
その後Google Calendarの対象開催回を削除し、外部APIが失敗してもDBは復元しない。
失敗はログと警告へ残し、後続のDB→Google同期で孤立した予定を削除して収束させる。
Vketロック中の子開催回が1件でも含まれる場合、主催者による親Eventの削除は
親子まとめて中止する。

日付移動時もDBを正として、元開催日のtombstone、Eventの日付・曜日、
未投稿の当日リマインド予約、キャッシュ無効化をtransactionで先に完了する。
その後Google Calendarを更新し、外部APIが失敗してもDB変更は維持してログと警告を残す。
後続のDB→Google同期が新しい開催日時へ収束させる。
移動対象が子開催回なら、定期生成のルール外掃除に削除されないよう親Eventとの紐付けを解除する。
親Eventは系列アンカーとして維持する。

## 検証手順

共有の起動中コンテナは使わず、worktreeを読み取り専用マウントした使い捨てコンテナで実行する。

```bash
docker compose run --rm --no-deps \
  -v "$PWD/app:/app:ro" \
  vrc-ta-hub \
  python manage.py test \
  event.tests.test_recurrence_override \
  event.tests.test_generate_recurring_events_command \
  event.tests.test_event_delete_view \
  event.tests.test_event_date_update \
  vket.tests.test_schedule_lock \
  vket.tests.test_vket.VketManageViewsTests
```

あわせて `python manage.py makemigrations --check --dry-run` と
`git diff --check` を実行し、モデルとmigrationの差分、空白エラーがないことを確認する。
