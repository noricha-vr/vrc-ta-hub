# Issue #141 調査メモ

## 背景

- Vket コラボの主催者向け操作の一部が `CommunityMember.Role.OWNER` 固定になっていた
- `CommunityMember` には `owner` と `staff` が存在するが、staff メンバーは同じ集会に所属していても参加登録やステージ登録完了を実行できなかった

## 調査結果

### 影響箇所

- `app/vket/views.py`
  - `CollaborationDetailView.get_context_data`
  - `ApplyView.get`
  - `ApplyView.post`
  - `StageRegisterView.post`
  - `PresentationDeleteView.post`

### 観測した原因

- アクティブ集会の所属確認には `_get_active_membership()` を使っている
- その後の権限判定で `membership.role == CommunityMember.Role.OWNER` を使っていたため、`staff` ロールが除外されていた
- `CollaborationDetailView` でも同じ owner 固定判定を使っていたため、staff では申請ボタン自体が表示されなかった

## 採用した対応

- 「その集会のメンバーであること」を主催者向け操作の条件として扱う helper に統一した
- `superuser` は従来どおり許可する
- owner 固定だった 403 メッセージを、実際の権限モデルに合う「参加集会のメンバーのみ」へ更新した

## 却下した代替案

- `owner or staff` を各ビューに直接書く案
  - 却下理由: 同じ分岐が 5 箇所に散っており、将来の権限変更時にズレやすい

## 検証方針

- staff メンバーで以下をテスト追加する
  - 参加登録画面 GET/POST
  - コラボ詳細の `can_apply`
  - ステージ登録完了
  - LT 削除
