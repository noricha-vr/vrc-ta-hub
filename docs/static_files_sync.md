# 静的ファイルのCloudflare R2同期手順

Djangoアプリケーションで使用する静的ファイル（画像、CSS、JSなど）を、本番環境で使用するCloudflare R2ストレージに同期（アップロード）する手順について解説します。

## 概要

本番環境（`DEBUG=False`）では、静的ファイルの配信にCloudflare R2（S3互換ストレージ）を使用しています。
ローカル開発環境で追加した画像などを本番環境に反映させるには、`python manage.py collectstatic` コマンドを使用して、ローカルの静的ファイルをR2バケットにアップロードする必要があります。

## 前提条件

- プロジェクトのルートディレクトリに `.env` ファイルが存在し、R2（AWS S3互換）の認証情報が設定されていること。
  - `AWS_ACCESS_KEY_ID`
  - `AWS_SECRET_ACCESS_KEY`
  - `AWS_STORAGE_BUCKET_NAME`
  - `AWS_S3_ENDPOINT_URL`
  - その他関連する環境変数

## 同期手順（ローカル環境から手動実行）

ローカルの開発環境からR2へファイルをアップロードする手順です。

### 1. 画像ファイルの配置

同期したい画像を適切なディレクトリに配置します。
例: `app/ta_hub/static/ta_hub/images/`

### 2. `docker-compose.yaml` の設定変更

`collectstatic` がR2に向くように、一時的にDockerの設定を変更します。

`docker-compose.yaml` を開き、`vrc-ta-hub` サービスの以下の箇所を変更します。

1.  `env_file` に `.env` を追加（本番用の環境変数を読み込むため）。
    ※ `.env.local` も必要な場合は両方有効にします。
2.  `DEBUG` を `False` に変更（`settings.py` でR2ストレージを使用する判定にするため）。

```yaml
    env_file:
      - .env
      - .env.local  # 必要に応じて
    environment:
      - DEBUG=False # True から False に変更
      - HTTP_HOST=0.0.0.0
```

### 3. 同期コマンドの実行

Dockerコンテナを一時的に起動し、`collectstatic` コマンドを実行します。

```bash
docker compose run --rm vrc-ta-hub python manage.py collectstatic --noinput
```

- `--noinput`: 確認プロンプトを表示せずに実行します。
- `--clear`: (オプション) 同期前に既存のファイルを削除したい場合に使用しますが、**通常は使用しないでください**。

### 4. 動作確認

アップロードされたファイルが公開URLからアクセスできるか確認します。

```bash
curl -I https://data.vrc-ta-hub.com/パス/ファイル名
# 例: https://data.vrc-ta-hub.com/ta_hub/images/my_image.png
```

HTTPステータスコード `200` が返ってくれば成功です。

### 5. 設定の復元

作業が終わったら、`docker-compose.yaml` を元の状態に戻します。

```yaml
    env_file:
      # - .env
      - .env.local
    environment:
      - DEBUG=True
```

## 自動同期について

本番環境へのデプロイフロー（CI/CD）が整備されている場合、デプロイ時に自動的に `collectstatic` が実行される設定になっていることが一般的です。その場合、手動での同期は不要です。
この手順は、CI/CDを通さずに緊急で静的ファイルを更新したい場合や、ローカルから直接アップロードを確認したい場合に使用します。
