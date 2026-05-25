# Issue 325 設定値集約調査

## 背景

Issue #325 では、公開サイトドメイン、OpenRouter API URL、キャッシュ TTL、アップロードサイズ上限が複数箇所に直書きされている点が指摘されていた。対象コードを確認した結果、告知文生成、Discord 通知、Markdown iframe 制限、ニュース構造化データ、トップページキャッシュ、イベントカレンダー URL キャッシュ、フォームのファイルサイズ検証に同種の値が分散していた。

## 観測結果

- `app/twitter/tweet_generator.py` は公開 URL と OpenRouter base URL を直接埋め込んでいた。
- `app/event/notifications.py` は通知用 URL の絶対化に公開サイト URL を直接使っていた。
- `app/event/libs.py` は iframe の自ドメイン判定と OpenRouter 呼び出しに固定文字列を使っていた。
- `app/news/models.py` / `app/news/views.py` はデフォルト OGP 画像 URL とカテゴリキャッシュ TTL を直接持っていた。
- `app/event_calendar/calendar_utils.py` と `app/ta_hub/views.py` は 1 時間キャッシュを `60 * 60` で直接指定していた。
- `app/event/forms.py` はサムネイル画像 10MB、PDF 30MB の上限を直接指定していた。

## 改善方針

保護対象である `app/website/settings.py` は変更せず、通常のアプリケーションコードとして `app/website/constants.py` を追加した。ここに `SITE_DOMAIN`、`SITE_URL`、`OPENROUTER_BASE_URL`、`CACHE_TTL_HOUR`、`MAX_THUMBNAIL_SIZE_BYTES`、`MAX_PDF_SIZE_BYTES` を集約し、対象コードから参照する構成にした。

`SITE_DOMAIN` は `SITE_DOMAIN`、`SITE_URL`、`APP_CANONICAL_HOST` の順に環境変数を参照し、未設定時は `vrc-ta-hub.com` を使う。`SITE_URL` は `SITE_URL` が未設定なら `https://{SITE_DOMAIN}` から組み立てる。

## 追加した軽微な改善

- 相対パスを公開サイト URL に変換する `build_site_url()` を追加し、通知・ニュース・告知文生成で URL 組み立てを統一した。
- iframe 制限の自ドメイン判定を `is_site_domain()` に集約し、完全一致またはドット区切りのサブドメインだけを自ドメインとして扱うようにした。
- `DEFAULT_NEWS_IMAGE_URL` を追加し、ニュース用のデフォルト画像 URL も集約先で確認できるようにした。

## 検証手順

- `rg` で対象アプリコード内の `vrc-ta-hub.com`、`https://openrouter.ai/api/v1`、`3600`、`60 * 60`、ファイルサイズ上限の直書きが `app/website/constants.py` 以外に残っていないことを確認する。
- `website.tests.test_site_constants` で環境変数読み込み、URL 組み立て、自ドメイン判定を検証する。
- 影響範囲の既存テストとして、Twitter 告知生成、イベントフォーム、ニュース、イベントカレンダー、トップページ関連テストを実行する。
