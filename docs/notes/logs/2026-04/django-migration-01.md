## tweet_queue テーブル未作成時の保存エラー回避
- 日付: 2026-04-05
- 関連: #175
- 状況: `EventDetail` / `Community` 保存時の `post_save` シグナルが `TweetQueue` を参照していた
- 問題: `tweet_queue` テーブル migration 未適用の環境で `django.db.utils.ProgrammingError: Table 'vrc_ta_hub.tweet_queue' doesn't exist` が発生し、承認・編集操作が 500 になった
- 対応: `twitter.signals` で `tweet_queue` テーブル存在確認を追加し、未作成時は warning を出してキュー生成だけをスキップするようにした。Community / EventDetail / slide share の回帰テストも追加した
- → how/django-migration.md に知識として追記済み
