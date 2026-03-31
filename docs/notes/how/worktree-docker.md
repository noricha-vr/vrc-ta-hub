# Worktree Docker パターン

## worktree の差分を Docker で確実に検証する
- 問題: `docker compose exec` で既存コンテナに入ると、bind mount が元リポジトリを向いたままで、現在の worktree の差分を読まないことがある
- 解決: `docker inspect <container>` で Mounts を確認し、ずれていたら `docker run --rm ... -v "$PWD/app":/app -v "$PWD/docs":/docs ...` の one-off コンテナで current worktree を明示マウントしてテスト・QAする
- 教訓: worktree 上の変更確認では「コンテナが本当に今の `$PWD` を見ているか」を先に確認する
