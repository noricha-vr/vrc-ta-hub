# Issue #135 EventDetail 日時ロック調査メモ

## 背景

Vket コラボ期間中に集会側が `EventDetail.start_time` / `duration` を更新できると、運営が調整した日程が崩れる。
Issue #135 では、Vket 期間内のイベントについて日時変更を運営限定にする必要がある。

## 観測結果

- `EventDetailUpdateView` は権限チェックのみで、Vket 期間中でも日時入力欄をそのまま編集できる
- `EventDetailForm` には Vket コラボ期間を判定するバリデーションがない
- `EventDetailAPIViewSet.perform_update()` も日時変更をそのまま保存する
- API では `event` フィールドも更新可能なため、未ロック詳細を Vket 期間中のイベントへ移しつつ日時変更する抜け道がある
- 既存ノート `docs/notes/how/vket-collab.md` では「日程・時間の調整は運営が行う」前提が整理されている

## 原因候補

- Vket 運営フローで確定した日程を、通常の EventDetail 更新フローが区別していない
- Web と API で共通の日時ロック判定がなく、更新経路ごとに制御漏れが起きやすい

## 改善方針

- `app/event/datetime_lock.py` に Vket 期間中の日時ロック判定を集約する
- Web 更新画面では `start_time` / `duration` を `disabled` にし、運営のみ変更可能な旨を表示する
- サーバー側ではフォームと API の両方で日時変更だけを弾き、テーマや本文の更新は継続して許可する
- superuser は従来どおり変更可能にする

## 却下した代替案

- `EventDetail.save()` で常時ブロックする
  - 却下理由: 管理画面や内部同期も巻き込んで責務が重くなり、変更経路ごとのエラーメッセージも出しづらい
- Web 側の `disabled` のみで対応する
  - 却下理由: API と改変 POST を防げない

## 検証手順

- `EventDetailForm` のテストで、Vket 期間中は非 superuser の日時変更が弾かれ、superuser は通ることを確認する
- Web 更新ビューのテストで、ロック中に案内文と disabled 属性が表示され、改変 POST でも日時が変わらないことを確認する
- API テストで、ロック中の日時変更が 400 になる一方、日時以外の更新は通ることを確認する
