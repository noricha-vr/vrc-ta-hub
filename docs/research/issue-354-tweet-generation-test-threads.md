# Issue #354 テスト中の TweetQueue 本文生成スレッド抑制 調査メモ

## 概要

`Community(status="approved")` や承認済み `EventDetail` の保存で `TweetQueue` が作成されると、
`twitter.signals._start_tweet_generation()` が本文生成用のバックグラウンドスレッドを起動していた。
SQLite テスト DB ではこの別スレッドが同じテーブルへアクセスし、`database table is locked` の
ランダムなログを出す原因になり得る。

## 観測結果

- `app/twitter/signals.py` は `queue_new_community_tweet()`、
  `queue_event_detail_tweet()`、`queue_slide_share_tweet()` から
  `_start_tweet_generation()` を呼び、`threading.Thread.start()` で非同期生成を開始する。
- `app/website/settings.py` は `TESTING` または `sys.argv` の `test` で SQLite テスト DB へ切り替える。
  `TESTING` 変数自体は環境変数由来のため、保護対象の設定ファイルは変更せず、
  シグナル側でも `sys.argv` の `test` をテスト実行判定に含めた。
- 既知の影響箇所である `app/user_account/tests/test_lt_application_views.py` と
  `app/ta_hub/tests/test_index_view_degraded_mode.py` は、承認済み `Community` / `EventDetail` を保存し、
  テスト対象外の TweetQueue 本文生成を副作用として起動していた。
- 当時は `event.tests.tweet_generation.TweetGenerationPatchMixin` で event テスト内のスレッドを
  明示的に抑制していたが、他アプリのテストへ適用漏れが起きたため Issue #539 で撤去した。

## 原因

本文生成スレッドの起動可否がテスト実行状態を見ておらず、`TweetQueue` 作成を検証したいだけの
テストでも実スレッドが開始されていた。`TestCase` の SQLite DB は同一プロセス内の別スレッドから
読まれる前提になっていないため、ロック競合が発生しやすい。

## 改善案と採用方針

`_start_tweet_generation()` で `generation_token` の保存までは従来通り行い、
global な `_should_skip_tweet_generation_thread()` により、`settings.TESTING=True` または
`manage.py test` 実行時だけスレッド起動前に返すようにした。

この方針は全アプリのテストに横断的に効くため、Issue #539 以降は個別テスト用 mixin を使わない。
一方で、Twitter シグナル自体のテストは `twitter.signals.threading.Thread` を明示的に
patch しているため、従来通り本番相当のスレッド起動経路を検証できる。

## 検証手順

- `python manage.py test twitter.tests.test_generation_guard.TweetGenerationThreadGuardTest` で、
  `generation_token` が保存され、`threading.Thread.start()` が呼ばれないことを確認する。
- `python manage.py test user_account.tests.test_lt_application_views ta_hub.tests.test_index_view_degraded_mode`
  で、既知の他アプリテストが副作用スレッドなしで通ることを確認する。
- `python manage.py test twitter.tests.test_signal_community twitter.tests.test_signal_event_detail twitter.tests.test_signal_slide_share`
  で、`threading.Thread` を明示 patch したシグナル系テストが従来通り起動経路を検証できることを確認する。
- `python manage.py test` 全体で `database table is locked` が出ないことを確認する。
