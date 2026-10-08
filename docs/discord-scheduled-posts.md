# Discord予約投稿

運営がHubで本文と投稿日時を指定し、固定のDiscordチャンネルへ予約投稿する。
Hubのお知らせや団体の提出状況との連動は行わない。

## 初期設定

1. 投稿先チャンネルの「チャンネル設定 → 連携サービス → ウェブフック」で専用Webhookを作成する。
2. アプリの環境変数に `DISCORD_SCHEDULED_WEBHOOK_URL` を設定する。管理者通知用の
   `DISCORD_WEBHOOK_URL` / `DISCORD_REPORT_WEBHOOK_URL` は流用しない。
3. `DISCORD_SCHEDULED_CHANNEL_URL` がWebhookの投稿先と一致することを確認する。
   既定値は `https://discord.com/channels/1143765879377645628/1304472925058891899`。
4. `python manage.py migrate` で予約テーブルを作成する。
5. Cloud Schedulerに以下のHTTPジョブを登録する。

| 項目 | 設定 |
|---|---|
| 頻度 | `* * * * *`（毎分） |
| タイムゾーン | `Asia/Tokyo` |
| URL | `https://vrc-ta-hub.com/discord-posts/process/` |
| HTTPメソッド | `POST` |
| HTTPヘッダー | `Request-Token`: アプリの `REQUEST_TOKEN` と同じ値 |

Webhook URLとRequest-Tokenは秘密情報として管理し、リポジトリには保存しない。
本番では既存のSecret Managerによる環境変数設定方法を使う。
Webhook未設定の間は画面に設定待ちを表示し、予約を作成できない。
この機能の追加だけではCloud Schedulerのジョブ作成や本番設定は行われない。

## 操作

staffまたはsuperuserでログインし、右上のアカウントメニューから「Discord予約投稿」を開く。
URLは `/discord-posts/`。

- 「新しい予約」で、本文と日本時間の投稿日時を入力して予約する。
- 本文はDiscordの上限に合わせて2,000文字相当まで。絵文字などは複数文字として数える場合がある。
- 本文中のURLやDiscordのメンション記法をそのまま投稿する。運営が明示したユーザー・ロール・
  `@everyone` / `@here` のメンションは、Discord側の権限の範囲で通知される。
- 送信開始前は、一覧から本文・日時の編集と取消ができる。
- 送信済みの予約には、実際の投稿日時とDiscordへのリンクを表示する。

投稿日時は「その時刻より前には送信せず、以降の定期処理で実行する」意味で、秒単位の定刻実行を保証しない。

## 送信結果

| 状態 | 扱い |
|---|---|
| 予約済み | 指定時刻以降の処理を待つ。編集・取消ができる |
| 送信中 | 処理が予約を確保した状態。編集・取消を止める |
| 送信済み | Discordから投稿IDを取得済み。受信者の既読は表さない |
| 失敗 | 設定・入力・Discordの応答を確認する。自動で再送しない |
| 要確認 | 通信切断などで投稿結果が確定できない。Discordを確認してから対応する |
| 取消済み | 送信しない |

Discordのレート制限（429）は、指定された待ち時間の後に再試行する。
結果が不明な通信エラーや、送信中に処理が停止した予約は自動再送せず「要確認」に移す。
複数の定期処理が同時に動いても、同じ予約を同時に送信しないようDBで処理権を確保する。

## 検証

既存のテスト用環境変数を設定し、`app/` で実行する。

```bash
python -m tests.offline_manage test discord_scheduler --noinput
python -m tests.offline_manage makemigrations --check --dry-run
```

送信テストはDiscord HTTPをモックし、既存のオフラインランナーで外向き通信を遮断する。
