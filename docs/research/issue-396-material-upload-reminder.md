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

## 翌日の Discord DM 依頼（Issue #873）

メールの送信と送信済みログの作成が成功した後、以下の条件をすべて満たす場合だけ、集会アシスタントの Discord Bot から DM でも資料登録を依頼する。

- 備考の判定が「送る」で、スライドの URL と PDF がどちらも未登録。
- メールの宛先となる発表者が Discord と連携済みで、連携 ID が 17〜20 桁の半角数字。
- `select_community_discord_delivery()` が `bot` を選択する集会。`DISCORD_BOT_TOKEN` と `DISCORD_BOT_DELIVERY_COMMUNITY_IDS` に加え、設定完了した Bot 導入記録と有効な通知先チャンネルが必要。

DM には開催日、書式記号をエスケープした発表の題名、メールと同じ発表編集ページへのリンクを載せる。メンションは無効にする。dry-run と備考で除外した発表には送らない。1 週間後のリマインドは従来どおり。

`MaterialUploadReminderLog` の `discord_dm_status` と `discord_dm_sent_at` に結果を記録する。追加 migration はこの 2 列の追加だけで、既存行は空の状態・日時なしになる。

| DM の状態 | 意味 | 再送 |
|---|---|---|
| 空 | DM 未実施（既存行・対象外も含む） | 既存のメールログがあれば対象抽出から除外 |
| `sending` | 送信直前に保存済み。処理中断で残る場合もある | しない |
| `sent` | 送信成功。送信日時も保存 | しない |
| `forbidden` | チャンネル作成またはメッセージ送信が HTTP 403 | しない |
| `failed` | その他の HTTP エラー・通信失敗など | しない |

DM の HTTP 通信は自動再試行しない。DM に失敗してもメールの `status=sent` と送信日時を維持する。送信先 ID、URL、トークン、例外本文はログに出さず、発表 ID と DM の状態のみ記録する。

### 計測

- 指標: DM 状態ごとの件数。`forbidden` は受信不可、`failed` と残留する `sending` は要確認。
- 置き場: `MaterialUploadReminderLog` と `event.material_upload_reminders` の結果ログ。
- 確認方法: `python manage.py shell -c 'from django.db.models import Count; from event.models import MaterialUploadReminderLog; print(list(MaterialUploadReminderLog.objects.values("discord_dm_status").annotate(count=Count("pk"))))'`
- 見直し日: 本番反映の 7 日後。`sending` が残っていても自動再送はしない。
