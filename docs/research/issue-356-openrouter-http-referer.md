# Issue 356 OpenRouter HTTP-Referer 調査

## 背景

Issue #356 では、OpenRouter へ送信する `HTTP-Referer` が `build_site_url("/")` 由来になったことで、preview/staging 環境の `SITE_URL` や `APP_CANONICAL_HOST` が第三者に送られる可能性が指摘された。

## 観測結果

- `app/event/llm_service.py` は定期イベント日付生成で `OPENROUTER_EXTRA_HEADERS` を定義し、`build_site_url("/")` を `HTTP-Referer` に使っていた。
- `app/event/libs.py` はブログ記事生成時の OpenRouter 呼び出しで同じ `HTTP-Referer` をインライン定義していた。
- `app/twitter/tweet_generator.py` は告知ポスト生成時の OpenRouter 呼び出しで同じ `HTTP-Referer` をインライン定義していた。
- `SITE_URL` / `APP_CANONICAL_HOST` は preview/staging の公開リンク生成には必要だが、OpenRouter のランキング用ヘッダにそのまま使う必要はない。

## 原因

公開サイト URL の組み立てと OpenRouter へ送るサイト識別ヘッダが同じ `build_site_url("/")` に依存していたため、環境別ホストの上書きが外部 API の `HTTP-Referer` に伝播していた。

## 改善方針

保護対象の settings ファイルは変更せず、通常のアプリケーションコードである `app/website/constants.py` に `OPENROUTER_HTTP_REFERER` と `build_openrouter_extra_headers()` を追加した。デフォルトは公開 canonical URL の `https://vrc-ta-hub.com/` に固定し、必要な場合だけ `OPENROUTER_HTTP_REFERER` 環境変数で明示的に上書きできるようにした。

OpenRouter 呼び出し側は共通ヘッダ生成関数を使うように統一し、preview/staging 用の `SITE_URL` を使う通常のリンク生成とは責務を分けた。

## 検証手順

- `website.tests.test_site_constants` で `SITE_URL` が preview URL の場合でも `OPENROUTER_HTTP_REFERER` が公開 canonical URL のままになることを検証する。
- `website.tests.test_site_constants` で `OPENROUTER_HTTP_REFERER` 環境変数による明示上書きと末尾スラッシュ正規化を検証する。
- `event.tests.test_llm_service` で定期イベント日付生成の OpenRouter 設定が共通ヘッダ生成関数を参照していることを検証する。
- `rg` で OpenRouter の `HTTP-Referer` が `build_site_url("/")` から直接組み立てられていないことを確認する。
