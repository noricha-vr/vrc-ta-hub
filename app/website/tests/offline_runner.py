"""通常テストから外向きネットワーク接続を遮断するtest runner。"""

import ipaddress
import os
import socket
import sys
import threading
import traceback
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn
from unittest.mock import patch

from django.test.runner import DiscoverRunner

# 遮断イベント1件あたりに保持するスタックフレーム数。多すぎると出力が読めない。
BLOCKED_STACK_FRAME_LIMIT = 6
# 自プロジェクトのフレーム判定に使うルート（app/ ディレクトリ）。
_THIS_FILE = str(Path(__file__).resolve())
PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
# 報告するイベント件数の上限。同じモック漏れが大量に出ても出力を潰さない。
BLOCKED_EVENT_REPORT_LIMIT = 20


class ExternalNetworkBlockedError(ConnectionError):
    """許可されていない外向き接続を検出したことを表す。"""


@dataclass(frozen=True)
class BlockedNetworkEvent:
    """遮断した外向き通信の1件分の記録。"""

    operation: str
    target: str
    stack: tuple[str, ...]

    def format(self) -> str:
        """人が読める1件分のレポートを返す。"""
        lines = [f"- {self.operation}: {self.target}"]
        lines.extend(f"    {frame}" for frame in self.stack)
        return "\n".join(lines)


def _summarize_call_stack() -> tuple[str, ...]:
    """遮断元を追える程度にスタックを要約する。

    urllib3 等のライブラリ内フレームだけだとどのテストか分からないため、
    プロジェクト内 (PROJECT_ROOT 配下、本ファイル除く) の直近フレームを先頭に置く。
    ソース行そのものは出さない。webhook URL などのリテラルがCIログへ漏れるため。
    """
    frames = [
        frame for frame in traceback.extract_stack()
        if frame.filename != _THIS_FILE
    ]
    project_frames = [
        frame for frame in frames
        if frame.filename.startswith(PROJECT_ROOT + os.sep)
    ]
    selected = project_frames[-2:] + frames[-BLOCKED_STACK_FRAME_LIMIT:]
    lines: list[str] = []
    for frame in selected:
        line = f"{frame.filename}:{frame.lineno} in {frame.name}"
        if line not in lines:
            lines.append(line)
    return tuple(lines)


@dataclass
class BlockedNetworkRecorder:
    """遮断イベントをスレッドセーフに集める。"""

    events: list[BlockedNetworkEvent] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _suppressed: threading.local = field(default_factory=threading.local, repr=False)

    @contextmanager
    def suppressed(self) -> Iterator[None]:
        """意図的に遮断を発生させるテストの間だけ記録を止める（呼び出しスレッド限定）。"""
        previous = getattr(self._suppressed, "active", False)
        self._suppressed.active = True
        try:
            yield
        finally:
            self._suppressed.active = previous

    def record(self, operation: str, target: object) -> None:
        """遮断イベントを1件記録する。"""
        if getattr(self._suppressed, "active", False):
            return
        stack = _summarize_call_stack()
        with self._lock:
            self.events.append(
                BlockedNetworkEvent(operation=operation, target=repr(target), stack=stack),
            )

    def reset(self) -> None:
        """記録を初期化する。"""
        with self._lock:
            self.events.clear()

    def snapshot(self) -> list[BlockedNetworkEvent]:
        """記録済みイベントのコピーを返す。"""
        with self._lock:
            return list(self.events)

    def restore(self, events: Sequence[BlockedNetworkEvent]) -> None:
        """snapshot で退避した記録を書き戻す（入れ子でrunnerを動かすテスト用）。"""
        with self._lock:
            self.events[:] = list(events)


blocked_network_recorder = BlockedNetworkRecorder()


def _block(operation: str, target: object, message: str) -> NoReturn:
    """遮断イベントを記録してから ExternalNetworkBlockedError を送出する。"""
    blocked_network_recorder.record(operation, target)
    raise ExternalNetworkBlockedError(message)


def format_blocked_network_report(events: Sequence[BlockedNetworkEvent]) -> str:
    """遮断イベント一覧を stderr 向けのレポート文字列に整形する。"""
    header = (
        f"外向き通信が {len(events)} 件遮断されました "
        "(テストのモック漏れの可能性があります):"
    )
    shown = events[:BLOCKED_EVENT_REPORT_LIMIT]
    body = "\n".join(event.format() for event in shown)
    if len(events) > len(shown):
        body = f"{body}\n- ... ほか {len(events) - len(shown)} 件"
    return f"{header}\n{body}"


def _is_own_hostname(host: object) -> bool:
    """自ホスト名の解決かどうかを判定する。

    Django のメール送信は Message-ID 生成で socket.getfqdn() を呼び、
    コンテナ自身のホスト名を逆引きする。外向き通信ではないので許可する。
    """
    if isinstance(host, bytes):
        try:
            host = host.decode("ascii")
        except UnicodeDecodeError:
            return False
    if not isinstance(host, str):
        return False
    own = socket.gethostname().rstrip(".").lower()
    normalized = host.rstrip(".").lower()
    return bool(own) and normalized in {own, own.split(".", maxsplit=1)[0]}


def _is_loopback_host(host: object) -> bool:
    if isinstance(host, bytes):
        try:
            host = host.decode("ascii")
        except UnicodeDecodeError:
            return False
    if not isinstance(host, str):
        return False
    normalized = host.rstrip(".").lower()
    if normalized == "localhost":
        return True
    normalized = normalized.split("%", maxsplit=1)[0]
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _is_allowed_address(address: object) -> bool:
    if isinstance(address, (str, bytes)):
        return True
    return isinstance(address, tuple) and bool(address) and _is_loopback_host(address[0])


class OfflineNetworkDiscoverRunner(DiscoverRunner):
    """Unix socketとloopbackを許可し、それ以外のsocket接続を拒否する。"""

    def run_tests(self, test_labels, **kwargs):
        """外向き通信を遮断した状態でDjango test suiteを実行する。

        遮断が発生したら失敗数に1加算する。アプリ側が requests の例外を握りつぶすと
        テストは緑のまま通るため、suite の終了コードで検出できるようにする。
        件数は加算せず stderr レポート側に出す（失敗テスト数と混ざると誤読を招くため）。
        """
        blocked_network_recorder.reset()
        with block_external_network():
            failures = super().run_tests(test_labels, **kwargs)

        events = blocked_network_recorder.snapshot()
        if not events:
            return failures
        print(format_blocked_network_report(events), file=sys.stderr)
        return failures + 1


@contextmanager
def block_external_network() -> Iterator[None]:
    """Unix socketとloopback以外へのDNS・socket接続を一時的に拒否する。"""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo
    original_gethostbyaddr = socket.gethostbyaddr
    original_gethostbyname = socket.gethostbyname
    original_gethostbyname_ex = socket.gethostbyname_ex
    original_getnameinfo = socket.getnameinfo
    original_sendto = socket.socket.sendto
    original_sendmsg = getattr(socket.socket, "sendmsg", None)

    def guarded_connect(sock, address):
        if _is_allowed_address(address):
            return original_connect(sock, address)
        _block("connect", address, f"offline test blocked connection to {address!r}")

    def guarded_connect_ex(sock, address):
        if _is_allowed_address(address):
            return original_connect_ex(sock, address)
        _block("connect_ex", address, f"offline test blocked connection to {address!r}")

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host is None or _is_loopback_host(host):
            return original_getaddrinfo(host, *args, **kwargs)
        _block("getaddrinfo", host, f"offline test blocked DNS lookup for {host!r}")

    def guarded_gethostbyaddr(host):
        if _is_loopback_host(host) or _is_own_hostname(host):
            return original_gethostbyaddr(host)
        _block("gethostbyaddr", host, f"offline test blocked reverse DNS lookup for {host!r}")

    def guarded_gethostbyname(host):
        if _is_loopback_host(host):
            return original_gethostbyname(host)
        _block("gethostbyname", host, f"offline test blocked DNS lookup for {host!r}")

    def guarded_gethostbyname_ex(host):
        if _is_loopback_host(host):
            return original_gethostbyname_ex(host)
        _block("gethostbyname_ex", host, f"offline test blocked DNS lookup for {host!r}")

    def guarded_getnameinfo(address, flags):
        if _is_allowed_address(address):
            return original_getnameinfo(address, flags)
        _block(
            "getnameinfo", address,
            f"offline test blocked reverse DNS lookup for {address!r}",
        )

    def guarded_sendto(sock, data, *args):
        if args and _is_allowed_address(args[-1]):
            return original_sendto(sock, data, *args)
        if not args:
            return original_sendto(sock, data, *args)
        _block("sendto", args[-1], f"offline test blocked UDP send to {args[-1]!r}")

    def guarded_sendmsg(sock, buffers, *args):
        address = args[2] if len(args) >= 3 else None
        if address is None or _is_allowed_address(address):
            return original_sendmsg(sock, buffers, *args)
        _block("sendmsg", address, f"offline test blocked message send to {address!r}")

    with ExitStack() as stack:
        stack.enter_context(patch("socket.socket.connect", guarded_connect))
        stack.enter_context(patch("socket.socket.connect_ex", guarded_connect_ex))
        stack.enter_context(patch("socket.getaddrinfo", guarded_getaddrinfo))
        stack.enter_context(patch("socket.gethostbyaddr", guarded_gethostbyaddr))
        stack.enter_context(patch("socket.gethostbyname", guarded_gethostbyname))
        stack.enter_context(patch("socket.gethostbyname_ex", guarded_gethostbyname_ex))
        stack.enter_context(patch("socket.getnameinfo", guarded_getnameinfo))
        stack.enter_context(patch("socket.socket.sendto", guarded_sendto))
        if original_sendmsg is not None:
            stack.enter_context(patch("socket.socket.sendmsg", guarded_sendmsg))
        yield
