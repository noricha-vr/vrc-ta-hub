## Vket staff メンバーの参加操作権限を拡張
- 日付: 2026-03-31
- 関連: #141, #144
- 状況: Vket コラボで staff メンバーも参加登録やステージ登録完了を行いたい
- 問題: ApplyView など複数箇所が `CommunityMember.Role.OWNER` 固定で、同じ集会の staff が UI でも POST でも弾かれていた
- 対応: membership 存在判定 helper に統一し、apply GET/POST、`can_apply`、ステージ登録完了、LT削除の staff テストを追加した
- → how/vket-collab.md に知識として追記済み
