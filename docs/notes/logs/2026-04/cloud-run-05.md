## Cloud Run preview host の URL 形式入力を middleware 判定前に正規化した
- 日付: 2026-04-16
- 関連: #247
- 状況: `DisallowedHost` 対応済みの Cloud Run preview host 判定でも、proxy や実行環境の差分で `HTTP_X_FORWARDED_HOST` / `SERVER_NAME` が absolute URL や `host:port` 形式で届くと、正規表現判定をすり抜ける余地があった。
- 問題: preview host の許可方針自体は正しかったが、入力形式の揺れを吸収せずに regex へ渡していたため、raw host を canonical host へ寄せる前に取りこぼす可能性があった。
- 対応:
  1. `CanonicalCloudRunHostMiddleware` の判定前に `normalize_host()` を通した
  2. `HTTP_X_FORWARDED_HOST` / `SERVER_NAME` が `https://rev-...a.run.app:443/` 形式で届くケースの回帰テストを追加した
  3. middleware の判断コメントに `参照: PR #247` を残して背景追跡できるようにした
- 教訓: Host header の安全性は「何を許可するか」だけでなく「何形式で届くか」を揃えて初めて担保できる。Cloud Run preview host 対応は、判定対象も canonical host と同じ正規化 helper に寄せるとズレに強い。
