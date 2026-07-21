# Issue #522 X-Forwarded-Proto 調査

## 観測

Cloud Run 経由では TLS がコンテナ前段で終端される。nginx は Django へのプロキシ時に `X-Forwarded-Proto $scheme` を設定しており、コンテナ内接続の `http` で上流の値を上書きしていた。

Django の `SECURE_PROXY_SSL_HEADER` は `HTTP_X_FORWARDED_PROTO` が `https` のときに secure request と判定する。したがって `request.build_absolute_uri()` を使うページや通知が `http://` URL を生成し得る。

## 採用した対応

nginx が受信した `X-Forwarded-Proto` を `$http_x_forwarded_proto` で Django に透過する。Cloud Run が付与した `https` を保持でき、ローカル HTTP の場合も secure 扱いに固定しない。

回帰テストは nginx 設定を直接検査し、透過設定の存在と `$scheme` による上書きの不在を確認する。

## 却下した案

`X-Forwarded-Proto` を常に `https` に固定する案は、ローカル HTTP 経由のリクエストまで secure と判定するため採用しない。Django settings の変更は既存の secure proxy 設定で対応済みであり、保護対象でもあるため不要とした。

## 検証手順

1. `app/website/tests/test_nginx_config.py` を実行し、透過設定の回帰を確認する。
2. デプロイ後、`/llms.txt` と `/index.md` の内部リンクが `https://` であることを curl で確認する。
3. 開発環境では `http://localhost:8015/` が引き続き HTTP URL になることを確認する。
