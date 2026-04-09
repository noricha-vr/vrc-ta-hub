# Issue #238 調査メモ

## 対象

- 画面: アカウント情報更新
- URL: `https://vrc-ta-hub.com/account/update/`
- Issue: `noricha-vr/vrc-ta-hub#238`

## 観測結果

- `app/user_account/views.py` の `UserUpdateView` は `CustomUserChangeForm` を使っており、現状の更新対象は `user_name` と `email` のみ。
- `app/user_account/templates/account/user_update.html` も同じ 2 項目だけを個別描画している。
- `CustomUser` モデルには X / VRChat の本人識別情報を保存するフィールドが存在しない。
- `app/user_account/tests/test_forms.py` と `app/user_account/tests/test_views.py` には更新フォームの基本回帰テストがあるが、URL 正規化や追加項目の表示確認は未実装。

## 原因

- 保存先フィールドがないため、アカウント更新画面で X / VRChat の識別子を保持できない。
- 更新フォームに URL から ID へ正規化する責務がなく、URL 入力のまま保存する導線も存在しない。

## 改善案

- `CustomUser` に `x_id` と `vrchat_user_id` を追加し、空文字許容の文字列として保存する。
- `CustomUserChangeForm` に URL 正規化ロジックを集約し、X は `x.com` / `twitter.com` のプロフィール URL、VRChat は `vrchat.com/home/user/...` のプロフィール URLだけを受け付ける。
- 既存値再編集時の可読性を保つため、フォーム表示値は保存済み ID をそのまま見せる。
- 回帰防止として、フォーム単体テストと更新ビューの表示・保存テストを追加する。

## 却下した代替案

- URL 全文をそのまま保存する
  - 却下理由: Issue の要求が「保存時に ID へ正規化」であり、一覧や他画面で再利用する時も ID 保持のほうが扱いやすい。
- モデルの `save()` で自動正規化する
  - 却下理由: 管理画面や将来のバッチ更新まで暗黙変換が広がり、フォームごとのエラーメッセージ制御もしづらい。

## 検証手順

1. `CustomUserChangeForm` のテストで URL 正規化、ID 単体入力、不正 URL エラーを確認する。
2. `UserUpdateView` のテストで新規項目の表示、ヘルプ文、POST 後の正規化保存を確認する。
3. 既存のユーザー情報更新フローが壊れていないことを関連テストで確認する。
