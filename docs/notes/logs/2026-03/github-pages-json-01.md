## テストデータがVRChatワールドカレンダーに混入

- 日付: 2026-03-27
- 関連: なし（Discord報告: kimkim0106「ワールドのカレンダー表示が古い」「jsonが古そう」）
- 状況: VRChatワールド内のカレンダー表示が、toGithubPagesJson の sample.json を参照して集会情報を表示している
- 問題: 本番DBに TestUser Community (id=95, 96) が `status='approved'`, `tags=[]` で存在し、公開APIを通じてsample.jsonに混入。VRChatワールドのカレンダーにテストデータが表示された
- 対応:
  1. 本番DB修正: `UPDATE community SET status = 'rejected' WHERE id IN (95, 96)`
  2. APIフィルタ強化: `CommunityViewSet` に `.exclude(tags=[])` 追加（再発防止）
  3. テスト追加: `api_v1/tests/test_community_api.py` 新規作成（4テスト）
  4. GitHub Actions手動トリガー: `gh workflow run schedule.yml --repo noricha-vr/toGithubPagesJson`
  5. sample.json からテストデータ消失を確認（77→75件）
- → how/github-pages-json.md に知識として追記済み
