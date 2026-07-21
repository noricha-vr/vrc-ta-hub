# Issue #529 offline runner と Docker network-none の遮断結果

## 調査結果

通常の offline runner は、外向き DNS・TCP・UDP・`sendmsg` を
`ExternalNetworkBlockedError` で fail-closed にする。Docker の
`--network none` では Python の monkeypatch より先に OS が通信不能を返す場合があり、
これは同じく外向き通信が遮断された状態を表す。

2026-07-21 に使い捨て Linux コンテナで観測した結果は次のとおり。

| 操作 | offline runner | Docker `--network none` |
| --- | --- | --- |
| 外向き TCP / UDP / `sendmsg` | `ExternalNetworkBlockedError` | `OSError`、`errno.ENETUNREACH` (101) |
| 外向き名前解決 | `ExternalNetworkBlockedError` | `socket.EAI_AGAIN` または `socket.herror` の `h_errno` 2 (`TRY_AGAIN`) |
| IPv4 loopback | 許可 | 許可して疎通する |
| IPv6 loopback | 許可 | IPv6 loopback が利用可能な場合に疎通する |
| Unix datagram socket | 許可 | ネットワーク namespace に依存せず疎通する |

IPv6 は `socket.has_ipv6` が真でも namespace 側で利用できない環境があるため、既存の
テストは bind 失敗時に skip する。IPv4 loopback と Unix socket はネットワーク隔離中でも
ローカル IPC として成功することを必須とする。

## 採用方針

回帰テストは操作種別ごとに、runner の独自例外と network namespace が返す限定した errno
だけを遮断成功として扱う。任意の `OSError` を許容しないため、アドレス形式や socket の
利用方法を壊した場合はテスト失敗のままである。遮断実装そのものは変更せず、DNS/TCP/UDP/
`sendmsg` の fail-closed 契約を維持する。

## 検証方法

次の使い捨てコンテナで、通常の offline runner と Docker `--network none` の両方を実行する。

```bash
docker run --rm -v "$PWD/app:/app" \
  -e SECRET_KEY=test-secret-key-for-ci -e DEBUG=True -e TESTING=1 \
  -e ALLOWED_HOSTS=localhost,127.0.0.1 -e CSRF_TRUSTED_ORIGIN=https://localhost \
  -e GOOGLE_API_KEY=dummy -e GOOGLE_CALENDAR_ID=dummy-calendar-id@group.calendar.google.com \
  -e GEMINI_API_KEY=dummy -e OPENAI_API_KEY=dummy -e OPENROUTER_API_KEY=dummy \
  -e X_API_KEY=dummy -e X_API_SECRET=dummy -e X_ACCESS_TOKEN=dummy -e X_ACCESS_TOKEN_SECRET=dummy \
  -e DISCORD_WEBHOOK_URL=https://discord.invalid/offline-test -e REQUEST_TOKEN=dummy \
  -e EMAIL_FILE_PATH=/tmp/emails --entrypoint /bin/sh \
  vrc-ta-hub-offline-tests:issue-527 \
  -c 'cd /app && python -m tests.offline_manage test website.tests.test_offline_runner --testrunner=website.tests.offline_runner.OfflineNetworkDiscoverRunner --noinput'

docker run --rm --network none -v "$PWD/app:/app" \
  -e SECRET_KEY=test-secret-key-for-ci -e DEBUG=True -e TESTING=1 \
  -e ALLOWED_HOSTS=localhost,127.0.0.1 -e CSRF_TRUSTED_ORIGIN=https://localhost \
  -e GOOGLE_API_KEY=dummy -e GOOGLE_CALENDAR_ID=dummy-calendar-id@group.calendar.google.com \
  -e GEMINI_API_KEY=dummy -e OPENAI_API_KEY=dummy -e OPENROUTER_API_KEY=dummy \
  -e X_API_KEY=dummy -e X_API_SECRET=dummy -e X_ACCESS_TOKEN=dummy -e X_ACCESS_TOKEN_SECRET=dummy \
  -e DISCORD_WEBHOOK_URL=https://discord.invalid/offline-test -e REQUEST_TOKEN=dummy \
  -e EMAIL_FILE_PATH=/tmp/emails --entrypoint /bin/sh \
  vrc-ta-hub-offline-tests:issue-527 \
  -c 'cd /app && python -m tests.offline_manage test website.tests.test_offline_runner --testrunner=website.tests.offline_runner.OfflineNetworkDiscoverRunner --noinput'
```
