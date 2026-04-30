# Issue #288: Vket確定後の主催者向け日程・LTロック

## 背景

PR #138 で Vket 期間中の `Event` / `EventDetail` 側の日時変更と削除は制限済みだが、主催者向けの Vket 参加フォームと LT 個別削除フローには確定後ロックが残っていなかった。

## 観測結果

- `app/vket/views/helpers.py` の `_apply_permissions_for_user()` はコラボのフェーズと締切だけで編集可否を判定していた。
- `app/vket/views/apply.py` は `permissions.can_edit_schedule=True` の間、既存 `VketParticipation.requested_start_time` / `requested_duration` を POST 値で更新していた。
- 同ビューの formset 保存は `order` ベースの `update_or_create()` と未送信 order の削除で構成されており、既存 `VketPresentation.requested_start_time` の変更や削除が可能だった。
- `app/vket/views/presentation.py` の主催者向け `PresentationDeleteView` は、公開済み `published_event_detail` を持つ LT でも `EventDetail` ごと削除できる実装だった。

## 改善方針

- `VketParticipation.is_schedule_confirmed` を日程確定後ロックの単一判定にする。
- 主催者向けフォームでは、確定済み participation の日程フィールドと LT 開始時刻を disabled にする。
- 保存処理でも同じ判定を使い、HTML を迂回した POST でも日程と既存 LT 開始時刻を保持する。
- 確定済みまたは公開済み LT は、主催者向け個別削除で 403 にする。
- 管理者向け manage 画面の更新・削除フローは従来どおり維持する。

## 検証手順

```bash
docker compose run --rm -e EMAIL_FILE_PATH=/tmp/emails -e TESTING=1 vrc-ta-hub python manage.py test vket.tests.test_vket vket.tests.test_staff_access vket.tests.test_schedule_lock
docker compose run --rm vrc-ta-hub python manage.py check
```
