# Issue #542 Google→DB 同期デッドコード削除

## 現行の正規経路

イベント同期の正規経路は、`sync_calendar_events` が
`DatabaseToGoogleSync.sync_all_communities()` を呼ぶ DB→Google 方向である。

## 削除した参照

`register_calendar_events`、`delete_outdated_events`、それらの再export、`verify_timezone_fix` の利用、
およびGoogle→DB方向だけを検証する旧テスト4本を削除した。

## 確認コマンドとテスト計画

テストは、CI と同じダミー環境変数を渡す使い捨てコンテナで実行する。

```bash
docker run --rm --network none --read-only -w /app -v "$PWD/app:/app:ro" \
  --tmpfs /tmp --tmpfs /app/logs --tmpfs /app/media --tmpfs /var/log/supervisor \
  -e SECRET_KEY=test-secret-key-for-ci -e DEBUG=True -e TESTING=1 -e ALLOWED_HOSTS=localhost,127.0.0.1 -e CSRF_TRUSTED_ORIGIN=https://localhost \
  -e GOOGLE_API_KEY=dummy-google-api-key -e GOOGLE_CALENDAR_ID=dummy-calendar-id@group.calendar.google.com -e GEMINI_API_KEY=dummy-gemini-api-key -e OPENAI_API_KEY=dummy-openai-api-key -e OPENROUTER_API_KEY=dummy-openrouter-api-key \
  -e X_API_KEY=dummy-x-api-key -e X_API_SECRET=dummy-x-api-secret -e X_ACCESS_TOKEN=dummy-x-access-token -e X_ACCESS_TOKEN_SECRET=dummy-x-access-token-secret -e DISCORD_WEBHOOK_URL=https://discord.invalid/offline-test -e REQUEST_TOKEN=dummy-request-token -e EMAIL_FILE_PATH=/tmp/emails \
  vrc-ta-hub-vrc-ta-hub:latest \
  python manage.py test event.tests.test_event_sync --noinput --verbosity=2
```

## 判断

未使用の Google→DB 実装は同期方向の誤認とDB書き換えの保守リスクになるため、互換利用元のない参照・テストを含めて削除する。
