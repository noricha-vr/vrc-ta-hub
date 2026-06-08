# Issue 407: LT 告知ポストの登壇者 X アカウント表示

## 調査対象

- `app/twitter/tweet_generator.py`
  - `generate_lt_tweet()` は LT 告知ポストの LLM prompt を組み立てる。
  - `_fallback_presentation_tweet()` は LLM 出力が X 投稿制約を満たせない場合の決定的 fallback を作る。
  - `generate_daily_reminder_tweet()` は当日リマインダーの発表一覧を prompt に渡す。
- `app/event/models.py`
  - `EventDetail.applicant` は LT 申請者の `CustomUser` を参照する。
- `app/user_account/models.py`
  - `CustomUser.x_account` は `@` なしの X ハンドルを保持する。
- `app/event/views/lt_application.py`
  - LT 申込時に `speaker` と `x_account` は申請者ユーザーへ同期保存される。

## 原因候補と観測結果

- 既存の LT 告知 prompt は `EventDetail.speaker` のみを発表者として渡しており、`EventDetail.applicant.x_account` を参照していなかった。
- 当日リマインダーの発表一覧も `detail.speaker` のみで構成されていた。
- fallback 生成も `event_detail.speaker` のみを本文に入れていたため、LLM が失敗した場合も X アカウントは出力されなかった。
- `x_account` はモデルバリデーション上 `@` なしで保存されるため、投稿本文・prompt では表示時だけ `@` を付けるのが既存データ構造に合う。

## 採用した改善案

- `EventDetail` から告知本文用の登壇者表記を作る helper を `tweet_generator.py` に追加した。
- helper は `speaker` に敬称を付け、`applicant.x_account` がある場合だけ `（@handle）` を後置する。
- LT 告知 prompt、当日リマインダーの発表一覧、LT/SPECIAL 共通 fallback で同じ helper を使い、表記ゆれを避ける。

## 却下した代替案

- `EventDetail` に X アカウント専用フィールドを追加する案は、既存の `EventDetail.applicant -> CustomUser.x_account` で要件を満たせるため採用しない。
- LLM 出力後に文字列置換で `@handle` を追記する案は、本文行数と weighted length の制約を壊しやすいため採用しない。

## 検証手順

- `generate_lt_tweet()` の prompt に、X アカウントありの申請者で `テスト太郎さん（@speaker_vr）` が含まれることをテストする。
- applicant なしの場合は `テスト太郎さん` のみになり、X アカウント表記が混入しないことをテストする。
- 当日リマインダーの発表一覧に X アカウントが含まれることをテストする。
- LLM が X 投稿制約違反の本文を返し続けた場合の fallback にも X アカウントが含まれることをテストする。
- 既存の weighted length 制限テストを含む `twitter.tests.test_auto_tweet` を実行する。
