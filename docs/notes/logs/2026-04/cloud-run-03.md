## Cloud Run preview host 判定を明示 service 名に寄せた
- 日付: 2026-04-07
- 関連: #222
- 状況: `rev-24d1224---vrc-ta-hub-...a.run.app` で `DisallowedHost` が再度観測され、Django 側と nginx 側の preview host 判定をもう一段そろえる必要があった。
- 問題: Django middleware が runtime 依存の service 名を前提にしていて、nginx の `vrc-ta-hub` / `vrc-ta-hub-dev` 明示ルールとズレる余地があった。
- 対応: preview host 判定を明示 service 名リストベースに変更し、nginx 設定テストも同じ正規表現ソースを参照するようにした。
- → how/cloud-run.md に知識として追記済み
