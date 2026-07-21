# Issue #529 offline runner と Docker network-none の遮断結果

## 調査結果

通常の offline runner は、外向き DNS・TCP・UDP・`sendmsg` を
`ExternalNetworkBlockedError` で fail-closed にする。Docker の
`--network none` では Python の monkeypatch より先に OS が通信不能を返す場合があり、
これは同じく外向き通信が遮断された状態を表す。

2026-07-21 に使い捨て Linux コンテナで観測した結果は次のとおり。

| 操作 | offline runner | Docker `--network none` |
| --- | --- | --- |
| 外向き TCP / UDP / `sendmsg` | `ExternalNetworkBlockedError` | `OSError` かその派生型、かつ `errno == errno.ENETUNREACH` (101) |
| 外向き名前解決 | `ExternalNetworkBlockedError` | `socket.gaierror` かつ `errno == socket.EAI_AGAIN` (-3)、または `socket.herror` かつ `errno == 2` (`TRY_AGAIN`) |
| IPv4 loopback | 許可 | 許可して疎通する |
| IPv6 loopback | 許可 | IPv6 loopback が利用可能な場合に疎通する |
| Unix datagram socket | 許可 | ネットワーク namespace に依存せず疎通する |

IPv6 は `socket.has_ipv6` が真でも namespace 側で利用できない環境があるため、既存の
テストは bind 失敗時に skip する。IPv4 loopback と Unix socket はネットワーク隔離中でも
ローカル IPC として成功することを必須とする。

## 採用方針

回帰テストは操作種別ごとに、runner の独自例外と network namespace が返す限定した errno
だけを遮断成功として扱う。DNS は例外型と errno の組み合わせまで照合するため、同じ数値 2 の
`FileNotFoundError(errno.ENOENT)` は成功扱いしない。TCP / UDP / `sendmsg` も任意の
`OSError` ではなく `errno.ENETUNREACH` だけを許容する。これにより、アドレス形式や socket の
利用方法を壊した場合はテスト失敗のままである。遮断実装そのものは変更せず、DNS/TCP/UDP/
`sendmsg` の fail-closed 契約を維持する。

## 検証方法

通常の offline runner は標準のテスト入口で実行する。

```bash
scripts/run_tests.sh website.tests.test_offline_runner
```

OS 側の分岐は、現在の checkout から使い捨て image を build し、Docker network namespace
だけで外向き通信を遮断して実行する。

```bash
docker build -t vrc-ta-hub-offline-tests:issue-529 .

docker run --rm --network none \
  -e SECRET_KEY=offline-boundary-test -e DEBUG=True -e TESTING=1 \
  -e GOOGLE_API_KEY=dummy -e GOOGLE_CALENDAR_ID=dummy \
  -e GEMINI_API_KEY=dummy -e REQUEST_TOKEN=dummy \
  --entrypoint /bin/sh vrc-ta-hub-offline-tests:issue-529 \
  -c 'cd /app && python manage.py test website.tests.test_offline_runner --testrunner=django.test.runner.DiscoverRunner --noinput'
```

raw コマンドは `tests.offline_manage` と `OfflineNetworkDiscoverRunner` を意図的に使わず、
Docker network namespace が返す型・errno の分岐を直接検証する。通常 CI と標準入口は
引き続き両方の Python 側遮断を使う。
