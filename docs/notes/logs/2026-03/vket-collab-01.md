## CommunityMemberロール不正値で Vket ApplyView 403
- 日付: 2026-03-21
- 関連: なし
- 状況: kimkim0106 に ITインフラ集会の主催者権限を付与し、ローカルでVketテストできるようにしたかった
- 問題: `role='organizer'` を設定したが、正しい値は `CommunityMember.Role.OWNER` = `'owner'`。Django TextChoices は不正値でもバリデーションエラーにならず、`/vket/1/apply/` で 403 Forbidden が発生
- 対応: `CommunityMember.Role.OWNER` を使って再設定し解決。TextChoices の値は Enum 定数経由で設定するルールを確立
- → how/vket-collab.md に知識として追記済み
