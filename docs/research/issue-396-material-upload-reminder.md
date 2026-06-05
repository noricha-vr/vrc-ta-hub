# Issue 396: 発表資料アップロード依頼メール

## 調査対象

- 既存の発表申請通知は `app/event/notifications.py` で `send_mail` と HTML テンプレートを使っている。
- 通常の発表申請の備考は `EventDetail.additional_info` に保存される。
- Vket コラボ由来の備考は `VketPresentation.published_event_detail` から `VketParticipation.organizer_note` を参照できる。
- 既存の OpenRouter 利用は `OPENROUTER_API_KEY` と `GEMINI_MODEL` を参照しており、モデル名は設定値で管理されている。

## 原因候補と改善案

発表後に資料アップロードを促す仕組みがなく、発表者への案内は承認時メール内の補足に留まっていた。
また、申請時に「資料公開なし」「動画公開なし」などの意向が書かれていても、送信前に除外する判断点がなかった。

改善案として、開催日翌日を対象にする management command を追加し、対象抽出、備考取得、LLM 判定、メール送信、二重送信防止ログ記録をサービスに分離する。

## 採用判断

- 通常申請と Vket 同期済み発表の両方を対象にする。
- 送信済みまたは備考除外済みの発表は `MaterialUploadReminderLog` で一意に記録し、再実行で二重送信しない。
- LLM エラー時は誤送信を避けるため送信しない。ただしログは確定しないため、次回実行で再判定できる。
- 既に `slide_url` または `slide_file` がある発表は資料登録済みとして対象外にする。
- モデル名は変更せず、既存の `GEMINI_MODEL` と OpenRouter 互換設定を参照する。

## 検証手順

追加テストで以下を固定する。

- `additional_info` の公開不可文言は送信しない。
- `organizer_note` の公開不可文言は送信しない。
- 「資料は後日公開予定」「動画は公開なしだがスライドは共有可」は送信対象にできる。
- LLM エラー時は送信しない。
- 宛名、締切文言なし、参考ページ URL をメール本文で確認する。
- dry-run はメール送信とログ記録を行わず、対象と判定結果を表示する。
