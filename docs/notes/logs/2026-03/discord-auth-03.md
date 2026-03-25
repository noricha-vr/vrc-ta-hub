## 元の owner アカウントから Discord を切り離して個人アカウントへ移した
- 日付: 2026-03-24
- 関連: #108
- 状況: `マネジメント集会` owner アカウントに `madao` の Discord が付いており、staff 本人アカウントとして扱うには role と認証の責務が混ざっていた。
- 問題: owner をそのまま staff に落とすと集会 owner が不在になるため、単純な role 変更では整理できなかった。
- 対応: `madao7720 / gda.mtuser@gmail.com` の個人アカウントを新規作成し、Discord `SocialAccount` を元の owner アカウントから移したうえで、`マネジメント集会` `ML集会` `CS集会` `データサイエンティスト集会` に staff として所属させた。元アカウントは `マネジメント集会 owner` のみ残した。
- → how/discord-auth.md の運用判断を適用
