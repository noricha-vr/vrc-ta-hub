# Issue #499 disabled の参加希望日バリデーション調査

## 原因と観測

`VketApplyForm` はコラボ期間内の `Event` の日付だけを `requested_date` の choices にする。
日程確定済みの参加は主催者に日程編集を許可しないため、同フィールドは disabled になる。Django は disabled フィールドの POST 値を使わず initial 値を検証するが、choices 検証自体は実行する。そのため、期間内に Event がない集会では、既存の参加希望日が choices に存在せず、発表情報や備考だけの更新もフォーム全体が invalid になる。

## 採用方針

日程編集不可かつ既存参加に `requested_date` がある場合だけ、その日付を choices に補う。既に Event 由来の choices にあれば追加しない。編集可能な状態の choices は従来どおり Event の日付だけであり、主催者が新たに日付を選べる範囲は変わらない。

## 却下した代替案

`requested_date` を常に `required=False` にして clean で既存値へ差し替える案は、編集可能な申請の必須入力・選択肢検証を弱めるため採用しない。日程ロック時に限って既存値を有効な選択肢にする方が、Django の disabled フィールドの検証経路と既存の入力制約を両立できる。

## 検証

`Event` がない集会に確定済みの `VketParticipation` と既存発表を作成し、主催者が備考・発表者・テーマだけを POST する回帰テストを追加する。302 応答、備考と発表の更新、希望日・希望時刻・希望時間・確定日程・既存発表開始時刻の不変を確認する。

実行コマンド:

```bash
docker run --rm -v "$PWD/app:/code/app" -v "$PWD/pyproject.toml:/code/pyproject.toml:ro" \
  -w /code/app \
  -e SECRET_KEY=test-secret-key-for-ci -e DEBUG=True -e TESTING=1 \
  -e ALLOWED_HOSTS=localhost,127.0.0.1 -e CSRF_TRUSTED_ORIGIN=https://localhost \
  -e GOOGLE_API_KEY=dummy-google-api-key -e GOOGLE_CALENDAR_ID=dummy-calendar-id@group.calendar.google.com \
  -e GEMINI_API_KEY=dummy-gemini-api-key -e OPENAI_API_KEY=dummy-openai-api-key \
  -e REQUEST_TOKEN=dummy-request-token -e EMAIL_FILE_PATH=/tmp/emails \
  vrc-ta-hub-test:latest python manage.py test vket.tests.test_vket.VketApplyFlowTests
```
