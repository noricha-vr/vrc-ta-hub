# Issue #293: Vket参加申請の参加状態更新導線

## 要件

- 運営スタッフがVket管理画面から参加申請を `active / declined / withdrawn` に変更できる。
- `declined` と `withdrawn` は一覧で明確に判別できる。
- 非 `active` の参加申請は公開同期対象外にする。
- 操作権限は `is_superuser or is_staff` に限定する。

## 調査結果

- `VketParticipation.lifecycle` は `active / declined / withdrawn` の選択肢を既に持っていた。
- `ManageParticipationUpdateView` は日程確定、運営備考、進捗更新のみを扱い、`lifecycle` を更新していなかった。
- `vket/manage.html` は非 `active` の表示バッジだけを持ち、運営が状態を変更するフォームはなかった。
- `ManagePublishView` は `lifecycle=ACTIVE` の参加だけを公開対象にしており、状態変更導線を追加すれば公開除外の設計は成立する。

## 対応方針

- 既存の管理更新URLを再利用し、`action=update_lifecycle` のPOSTだけ参加状態更新として処理する。
- 日程確定フォームとは分離し、状態変更時に確定日程・進捗・公開済みイベントを副作用で変更しない。
- 管理画面に参加状態列と状態変更フォームを追加し、送信時はブラウザ確認を挟む。
- 公開同期の active フィルタは既存実装を維持し、回帰テストで固定する。

## 検証手順

- 管理画面の表示で `不参加` / `辞退` が判別できることをDjangoビューのレスポンスで確認する。
- superuser と staff が `lifecycle` を変更でき、一般ユーザーが403になることを確認する。
- `declined` / `withdrawn` の参加申請が `ManagePublishView` で `Event` 作成・`published_event` 紐付け・`DONE` 更新されないことを確認する。

## 注意点

- 既に公開済みの参加申請を後から非 `active` にした場合、今回の変更は既存 `Event` を削除しない。Issueの受け入れ条件は公開同期対象外であり、公開済みイベントの取り下げは別途運用または追加Issueで扱う。
- CI/CD、デプロイ、認証、設定の保護対象ファイルは変更していない。
