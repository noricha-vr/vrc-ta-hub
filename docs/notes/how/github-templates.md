# GitHub テンプレート運用

## PR / Issue テンプレートを標準形へそろえる
- 問題: リポジトリごとに `.github/pull_request_template.md` の項目がばらつき、Issue テンプレート未整備のまま `gh issue create` 運用に入ると、AI エージェントと人間の両方で記述粒度が崩れやすい。
- 解決: `github-template-setup` スキルで `.github/pull_request_template.md` と `.github/ISSUE_TEMPLATE/*.md` をまとめて生成し、既存 PR テンプレートを標準形へ寄せるときは `--force` を使う。
- 教訓: 既存テンプレートがある repo では最初に `--dry-run` で衝突を確認し、標準化すると決めたら `--force --no-commit` で反映して、その作業の文脈に合わせた commit にまとめるのが扱いやすい。

## 直接 main に入れるときの最小手順
1. `git checkout main`
2. `git pull --ff-only origin main`
3. `bash ~/.claude/skills/github-template-setup/setup.sh <repo> --force --no-commit`
4. ノート更新や補足ファイルをまとめて `git commit` / `git push origin main`

## 注意点
- `config.yml` は `blank_issues_enabled: false` になるので、空 Issue を防ぐ運用前提。
- 既存テンプレートを温存したい repo では `--force` を使わない。
