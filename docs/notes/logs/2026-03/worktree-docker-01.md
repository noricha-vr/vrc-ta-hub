## worktree の Docker 検証先ずれ
- 日付: 2026-03-31
- 関連: #136, #143
- 状況: Issue #136 の実装後に Docker 上でテストと QA を行った
- 問題: `docker compose exec vrc-ta-hub ...` の既存コンテナが `/Users/ms25/project/vrc-ta-hub/app` を bind mount していて、`/Users/ms25/worktrees/vrc-ta-hub` の差分を読んでいなかった
- 対応: `docker inspect vrc-ta-hub` で Mounts を確認し、`docker run --rm --network my_network --env-file .env.local -e DEBUG=True -e PYTHONPATH=/app -e DEFAULT_GENERATE_MONTHS=1 -v "$PWD/app":/app -v "$PWD/docs":/docs vrc-ta-hub-vrc-ta-hub ...` の one-off コンテナで current worktree を直接マウントして再検証した
- → how/worktree-docker.md に知識として追記済み
