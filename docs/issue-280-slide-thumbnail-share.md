# Issue #280: ブログサムネイル・SNS共有画像の調査結果

## 要件

新しいLT資料のPDFスライドをアップロードしたとき、ブログ記事のサムネイルとSNS共有時の画像を集会ポスターではなくスライド1枚目にする。

## 調査結果

- `EventDetail.thumbnail_image` は既に存在し、`event/detail.html` の本文上部・`og:image`・`twitter:image` は `thumbnail_image` を集会ポスターより優先していた。
- PDF先頭ページからJPEGサムネイルを作る処理は `event.libs.ensure_pdf_thumbnail()` として実装済みだった。
- ただし通常のPDFアップロード保存では `ensure_pdf_thumbnail()` が呼ばれず、記事生成時の `apply_blog_output_to_event_detail()` でだけ補完されていた。
- X自動投稿キューの添付画像は `twitter.tweet_generator.get_poster_image_url()` 経由で集会ポスター固定だった。

## 原因

サムネイル生成とOGP表示の部品は揃っていたが、PDFアップロード保存フローとX投稿画像フローが `EventDetail.thumbnail_image` に接続されていなかった。

## 対応方針

- `EventDetailForm` と `LTApplicationEditForm` の `save()` で `ensure_pdf_thumbnail(instance, save=True)` を呼び、未設定かつPDFありの場合にPDF先頭ページサムネイルを保存する。
- X投稿画像選択用に `get_tweet_image_url(queue_item)` を追加し、`event_detail.thumbnail_image` を優先、未設定時のみ集会ポスターへフォールバックする。
- 既存のOGP/Twitter Card metaは `thumbnail_image` 優先であるため、テンプレート構造は維持する。

## 検証方法

- `event.tests.test_event_detail_form` でフォーム保存時にPDFサムネイル補完が呼ばれることを確認する。
- `event.tests.test_generate_blog` でPDF先頭ページから16:9 JPEGが作られることを確認する。
- `event.tests.test_event_detail_template` で `og:image` / `twitter:image` と本文上部画像が `thumbnail_image` を参照することを確認する。
- `twitter.tests.test_auto_tweet.GetPosterImageUrlHelperTest` でX投稿画像が `EventDetail.thumbnail_image` を優先し、未設定時は集会ポスターへフォールバックすることを確認する。
