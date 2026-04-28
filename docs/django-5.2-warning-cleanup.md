# Django 5.2 移行前 deprecation warning 調査

## 概要

Issue #264 の受け入れ条件に沿って、Django 4.2 環境で `-Wa` を付けた warning 表示を確認した。

結論として、プロジェクト由来の Django deprecation warning は `app/website/settings.py` の storage 設定から発生している。該当ファイルは今回の保護対象ファイルに含まれるため、この PR では設定変更を行わない。

## 実行コマンドと結果

### manage.py check

```bash
COMPOSE_PROJECT_NAME=vrc-ta-hub-issue1660 docker compose -f docker-compose.yaml run --rm --no-deps -T vrc-ta-hub python -Wa manage.py check
```

結果:

- 終了コード: 0
- system check: `System check identified no issues (0 silenced).`
- warning:
  - `RemovedInDjango51Warning: The DEFAULT_FILE_STORAGE setting is deprecated. Use STORAGES instead.`
  - `RemovedInDjango51Warning: The STATICFILES_STORAGE setting is deprecated. Use STORAGES instead.`

発生元:

- `app/website/settings.py`

判断:

- Django 4.2 での deprecation warning で、Django 5.2 移行前に `STORAGES` へ移行する必要がある。
- ただし `**/*settings*.py` は今回の保護対象ファイルであるため、この作業では変更しない。

### CI 相当テスト

CI の `.github/workflows/ci.yml` に列挙されている test job と同じモジュール一覧を `python -Wa manage.py test ...` で実行した。

観測結果:

- 既存コンテナでの実行では、`website.tests.test_nginx_config` の 3 件が `nginx-app.conf` の参照パスで失敗した。
- 失敗理由は、Docker 開発環境では `./app:/app` のみがマウントされ、リポジトリ直下の `nginx-app.conf` が `/app` から見えないため。
- テストコードは、CI checkout ではリポジトリ直下の `nginx-app.conf`、Docker image 内では `/etc/nginx/sites-available/default` を読むように修正した。

修正後の確認:

```bash
COMPOSE_PROJECT_NAME=vrc-ta-hub-issue1660 docker compose -f docker-compose.yaml run --rm --no-deps -T vrc-ta-hub python -Wa manage.py test website.tests.test_nginx_config
```

結果:

- 3 tests
- OK

## warning の分類

### プロジェクト由来

| warning | 発生元 | 対応 |
| --- | --- | --- |
| `DEFAULT_FILE_STORAGE setting is deprecated` | `app/website/settings.py` | 保護対象ファイルのため未修正。`STORAGES["default"]` へ移行が必要。 |
| `STATICFILES_STORAGE setting is deprecated` | `app/website/settings.py` | 保護対象ファイルのため未修正。`STORAGES["staticfiles"]` へ移行が必要。 |

### third-party 由来

| warning | 発生元 | 対応 |
| --- | --- | --- |
| `datetime.datetime.utcnow() is deprecated` | `botocore/auth.py` | Python 3.12 の deprecation warning。`boto3` / `botocore` 更新候補として後続で確認する。 |

### deprecation ではない warning

CI 相当テスト中に drf-spectacular の schema 生成 warning が出ているが、Django 5.2 deprecation warning ではないため、Issue #264 の修正対象外とした。

## 後続対応

1. 保護対象ファイルの修正が許可された作業で、`app/website/settings.py` の `DEFAULT_FILE_STORAGE` / `STATICFILES_STORAGE` を `STORAGES` に移行する。
2. 依存更新作業で `boto3` / `botocore` の Python 3.12 `utcnow()` warning が解消されるバージョンを確認する。
3. 設定移行後に、以下を再実行する。

```bash
python -Wa manage.py check
python -Wa manage.py test <ci.yml の test job と同じモジュール一覧>
```
