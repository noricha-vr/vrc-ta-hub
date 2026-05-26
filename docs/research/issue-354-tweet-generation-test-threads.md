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
- `event.tests.tweet_generation.TweetGenerationPatchMixin` は event テスト内の明示的な抑制には有効だが、
  他アプリのテストへ適用漏れが起きる。

## 原因

本文生成スレッドの起動可否がテスト実行状態を見ておらず、`TweetQueue` 作成を検証したいだけの
テストでも実スレッドが開始されていた。`TestCase` の SQLite DB は同一プロセス内の別スレッドから
読まれる前提になっていないため、ロック競合が発生しやすい。

## 改善案と採用方針

`_start_tweet_generation()` で `generation_token` の保存までは従来通り行い、
`settings.TESTING=True` または `manage.py test` 実行時だけスレッド起動前に返すようにした。

この方針は、全アプリのテストに横断的に効き、個別テストへ mixin を追加し忘れるリスクを減らせる。
一方で、Twitter シグナル自体のテストは `twitter.signals.threading.Thread` を明示的に
patch しているため、従来通り本番相当のスレッド起動経路を検証できる。

## 検証手順

- `twitter.tests.test_auto_tweet.TweetGenerationThreadGuardTest` で、`manage.py test` 判定時は
  `generation_token` が保存され、`threading.Thread.start()` が呼ばれないことを確認する。
- `user_account.tests.test_lt_application_views` と
  `ta_hub.tests.test_index_view_degraded_mode` で、既知の他アプリテストが副作用スレッドなしで通ることを確認する。
- `twitter.tests.test_auto_tweet` のシグナル系テストで、`threading.Thread` を明示 patch した場合は従来通り
  `threading.Thread.start()` 経路を検証できることを確認する。
- `python manage.py test` 全体で `database table is locked` が出ないことを確認する。
