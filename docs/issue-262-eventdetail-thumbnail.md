# Issue #262 EventDetail サムネイル画像

## 調査結果

- `EventDetail` は `slide_file` を `FileField(upload_to='slide/')` で保存しており、既存の本番ストレージ設定に乗せれば Cloudflare R2 へ保存される。
- 編集フォームは `EventDetailForm` と `event/detail_form.html` で構成され、開催前 LT の詳細項目は JavaScript で折りたたまれる。
- 記事生成は編集フォーム保存時の `EventDetailCreateView` / `EventDetailUpdateView` と、詳細ページの `GenerateBlogView` の2経路がある。
- 記事ページは `event/detail.html` で、LT/SPECIAL の詳細情報 include より前にサムネイルを置けば要件の「詳細情報の上」を満たせる。

## 実装方針

- `EventDetail.thumbnail` は `ImageField(blank=True, null=True, upload_to='event_thumbnail/')` とし、既存レコードへ影響しない nullable migration にする。
- PDF 1枚目の画像化は `pypdfium2` を使う。`pdf2image` は poppler 依存が増えるため、Cloud Run での追加 OS パッケージ変更を避ける。
- 自動生成はサムネイル未設定かつ `slide_file` がある場合だけ実行し、ユーザーがアップロードした画像は上書きしない。
- 既存の画像配信に合わせ、表示時は `cf_resize` を通して Cloudflare Image Resizing を利用する。

## 検証手順

- `EventDetailForm` に `thumbnail` フィールドが含まれることをテストする。
- 記事生成時にサムネイル未設定なら PDF から生成されること、既存サムネイルを上書きしないことをユニットテストする。
- `event/detail.html` にサムネイル表示と OG/Twitter image の優先設定が入っていることをテンプレートテストで確認する。
- Docker の Django 実行環境で `python manage.py test event.tests.test_event_detail_thumbnail event.tests.test_event_detail_template` を実行する。
- `python manage.py makemigrations event --check --dry-run` で `event` アプリの migration 不足がないことを確認する。
