## 最終アセットのジャンル互換を確認
- 日付: 2026-03-27
- 関連: #120
- 状況: `Azukimochi/TaAGatheringListSystem` が最終表示先なので、PR #120 で直した `ジャンル` がアセット側で壊れないかを確認したかった
- 問題: アセット側 `EventInfo.Parse()` は `技術系` / `学術系` の完全一致でジャンルを判定し、それ以外は `Other` に落とす実装だった。そのため `技術系・学術系` のような複合文字列は互換性がない
- 対応:
  1. `Azukimochi/TaAGatheringListSystem` の README と `Scripts/EventInfo.cs` を確認し、元データが `sample.json` であることとジャンル判定ロジックを確認
  2. 公開 `sample.json` を確認し、`ジャンル` 値が `技術系/学術系/その他` のみであることを確認
  3. 公開 `sample.json` では `その他` が 2 件（`仮想学生集会`, `メタリエ (metariea)`）存在し、`gathering-list` API には含まれないことを確認
- → how/github-pages-json.md に知識として追記済み
