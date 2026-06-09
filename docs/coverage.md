# テストカバレッジ

## 意義

テストカバレッジは「コードのどの行がテストで実行されたか」を計測する指標です。VRC技術学術ハブでは、リグレッション検出が属人的だった現状を改善し、テストが手薄な領域を可視化するために `coverage.py` を導入しています。

カバレッジは「品質の絶対指標」ではなく、「テストの空白地帯を発見する手段」として扱います。100% を目指す運用はせず、リスクの高い領域から重点的にカバーする方針です。

## 目標値

- **現状**: 計測開始（fail_under = 0、強制しない）
- **目標**: 70%（段階的に引き上げる）

CI では数値を表示するのみで、閾値による失敗判定は行いません。閾値を強制すると「とりあえずテストを書く」圧力が生まれ、テストの質が下がる懸念があるためです。

## ローカルでの実行方法

Docker コンテナ内で `coverage` コマンドを使います。`docker-compose.yaml` で `pyproject.toml` をコンテナの `/pyproject.toml` にマウントしているため、`--rcfile=/pyproject.toml` で設定を読み込めます。

```bash
# 全テスト実行 + 未カバー行付きレポート
docker exec vrc-ta-hub-app coverage run --rcfile=/pyproject.toml manage.py test
docker exec vrc-ta-hub-app coverage report -m --rcfile=/pyproject.toml

# 特定アプリのテストでカバレッジ確認（--keepdb で高速化）
docker exec vrc-ta-hub-app coverage run --rcfile=/pyproject.toml manage.py test event.tests.test_convert_markdown --keepdb
docker exec vrc-ta-hub-app coverage report -m --rcfile=/pyproject.toml

# HTML レポート生成（詳細を視覚的に確認したい時）
docker exec vrc-ta-hub-app coverage html --rcfile=/pyproject.toml
# 出力先: app/htmlcov/index.html
```

`-m` フラグは未カバー行を表示します。どの行のテストが足りないか確認したい時に有用です。

## 計測対象と除外パターン

設定は `pyproject.toml` の `[tool.coverage.run]` セクションを参照してください。

| 対象 | 扱い | 理由 |
|------|------|------|
| `app/` 配下の Python | 計測 | プロダクトコード本体 |
| `*/migrations/*` | 除外 | Django 自動生成、テスト対象外 |
| `*/tests/*` | 除外 | テスト自身は計測しない |
| `*/conftest.py` | 除外 | pytest 設定ファイル |
| `manage.py` | 除外 | Django のエントリポイント |
| `*/settings/*` | 除外 | 設定ファイル、ロジックを含まない |

## CI 連携

`.github/workflows/ci.yml` の `test` job で `coverage run` 経由でテストを実行し、`coverage report` の結果を GitHub Actions ログに表示しています。PR ごとにカバレッジ % が見える状態です。

将来的にカバレッジバッジや差分カバレッジ（codecov 等）を導入する選択肢もありますが、現時点ではログ表示のみで運用します。

## 関連ドキュメント

- [pyproject.toml](../pyproject.toml) — coverage 設定の正本
- [.github/workflows/ci.yml](../.github/workflows/ci.yml) — CI でのカバレッジ計測
