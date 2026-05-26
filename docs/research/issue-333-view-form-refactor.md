# Issue 333: View/Form 巨大ファイル分割の調査と実装方針

## 対象と観測結果

- `app/twitter/views.py` はテンプレート CRUD、予約ポスト処理、X API 投稿、画像アップロード、TweetQueue 管理が同居していた。特に `post_scheduled_tweets()` は期限切れ判定、生成リトライ、投稿、失敗通知まで持っていた。
- `app/event/forms.py` はフォーム定義と PDF/画像ファイル検証が同居していた。`clean_slide_file()` と `clean_thumbnail_image()` は同じ検証関数を複数フォームから呼び出していた。
- `app/community/views/manage.py` はフォーム保存後の Discord 通知、承認/非承認メール、閉鎖時の関連データ削除を直接実行していた。
- `app/user_account/views.py` はログイン/登録、プロフィール設定、APIキー、LT申請編集が同居していた。

## 原因候補

- View が HTTP 入出力だけでなく外部 API 呼び出し、通知、ファイル加工、非同期処理起動まで保持していたため、変更単位とテスト単位が肥大化していた。
- 既存テストが `twitter.views.post_tweet` や `community.views.manage.cleanup_community_future_data` を patch しているため、単純に import 先を差し替えるとテスト互換性を失うリスクがあった。

## 改善案

- URL と既存 import パスは維持し、`views.py` は thin controller または public re-export として残す。
- X API 投稿、メディアアップロード、予約ポスト処理を `twitter/services/` に分離する。
- PDF/画像検証を `event/form_validators.py` に分離する。
- Community 管理フォームの副作用を `community/forms_processor.py` に分離する。
- Account 系 view は `user_account/view_modules/` に責務別分割し、`user_account/views.py` から再 export する。

## 検証手順

- `docker compose exec -T vrc-ta-hub python manage.py test twitter.tests.test_auto_tweet twitter.tests.test_tweet_queue_views twitter.tests.test_x_api_guard event.tests.test_pdf_validation event.tests.test_event_detail_form community.tests.test_views community.tests.test_community_create community.tests.test_switch_community user_account.tests.test_views user_account.tests.test_api_key_views user_account.tests.test_lt_application_views -v 2`
- 既存テストの patch パス互換性を守るため、`twitter.views.post_tweet` / `twitter.views.upload_media` と `community.views.manage.cleanup_community_future_data` は互換エントリとして残す。
