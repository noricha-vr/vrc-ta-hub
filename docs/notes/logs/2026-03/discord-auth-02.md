## Discord本体アカウントへ staff 所属だけを追加して復旧した
- 日付: 2026-03-24
- 関連: #108
- 状況: `madao` の Discord が `マネジメント集会` owner アカウントに付いている一方で、ML集会・CS集会・データサイエンティスト集会の owner / 既存 staff は別人の Discord とメールアドレスを持っていた。
- 問題: staff 本人の Discord 本体アカウントを探したいが、owner / 既存 staff をそのままマージすると別人アカウントを壊す危険があった。
- 対応: `Discord uid=955076172297957407 (madao7720)` を本体候補として扱い、`CommunityMember(role=staff)` を ML集会・CS集会・データサイエンティスト集会へ追加した。既存 owner / staff アカウントはマージしていない。
- → how/discord-auth.md に知識として追記済み
