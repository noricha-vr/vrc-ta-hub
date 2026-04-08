## Cloud Run host 正規化を helper ベースで強化した
- 日付: 2026-04-09
- 関連: #237
- 状況: `rev-24d1224---vrc-ta-hub-...a.run.app` の `DisallowedHost` 再発に対し、既存実装は `HTTP_HOST` 前提と `vrc-ta-hub.com` 固定のままで、proxy / env 差分の取りこぼし余地が残っていた。
- 問題: `APP_CANONICAL_HOST` や `HTTP_HOST` が URL / host:port で入るケース、または `SERVER_NAME` / `HTTP_X_FORWARDED_HOST` に raw preview host が残るケースを一箇所で吸収できていなかった。
- 対応:
  1. `website.hosts` に canonical host 正規化 helper を追加した
  2. settings と middleware の両方で同じ helper を使うようにした
  3. preview host 検知時は `HTTP_HOST` / `HTTP_X_FORWARDED_HOST` / `SERVER_NAME` をまとめて canonical host へ寄せるようにした
  4. middleware を `corsheaders` より前に移動し、順序もテストで固定した
- → how/cloud-run.md に知識として追記済み
