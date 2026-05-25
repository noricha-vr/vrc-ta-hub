# Issue 326: except pass 可視化の調査と対応

## 背景

`docs/research/refactor-plan.html` の H7 / M7 / 低優先度メモで、例外を無音で破棄する箇所が
運用時の原因追跡を妨げる問題として挙がっていた。
Issue #326 では `except Exception: pass` を具体例外と `logger.exception()` に寄せ、
正常系の `DoesNotExist` は意図をコメント化することが求められている。

## 観測結果

- `app/event/forms.py` はアップロード済みファイルの seek / content_type 設定失敗を
  無音で継続していた。
- `app/twitter/signals.py` は非同期ツイート生成自体の失敗ログに traceback がなく、
  失敗状態の保存に失敗した場合は完全に無音だった。
- `app/event/libs.py` と `app/event/management/commands/sanitize_event_detail_security.py` は
  URL 解析失敗時に安全側へ倒す fallback だが、入力値の追跡ができなかった。
- `app/ta_hub/libs.py` は画像ファイル open / seek / delete の失敗をスキップしており、
  壊れた画像やストレージ障害の切り分けが難しかった。
- `app/community/views/settings.py` は DoD のコメント追加対象だが、
  今回の保護対象 `**/*settings*.py` に該当するため変更しない。

## 改善方針

- fallback の戻り値と処理継続は維持し、例外種別を `OSError` / `ValueError` /
  `AttributeError` / `NoTranscriptFound` / `DatabaseError` など観測可能な範囲に限定した。
- ログはすべて `logger.exception()` に統一し、ファイル名、EventDetail ID、community ID、
  queue ID、iframe src など原因追跡に必要な識別子を添えた。
- 正常系の `DoesNotExist` は、削除直後や任意指定 ID の失効として継続する意図を
  コメントで明示した。

## 検証手順

以下の対象テストで、既存挙動が維持され、ログ追加の回帰テストが通ることを確認する。

```bash
docker compose exec -T vrc-ta-hub python manage.py test \
  event.tests.test_convert_markdown \
  event.tests.test_sanitize_event_detail_security_command \
  ta_hub.tests.test_resize_image \
  twitter.tests.test_auto_tweet \
  event.tests.test_recurrence_preview_api \
  community.tests.test_optimize_poster_images
```

加えて、時間が許せば全体確認として次を実行する。

```bash
docker compose exec -T vrc-ta-hub python manage.py test
```
