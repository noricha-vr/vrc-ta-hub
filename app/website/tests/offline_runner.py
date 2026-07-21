"""通常テストから外向きネットワーク接続を遮断するtest runner。"""

import ipaddress
import socket
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

from django.test.runner import DiscoverRunner


class ExternalNetworkBlockedError(ConnectionError):
    """許可されていない外向き接続を検出したことを表す。"""


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
        """外向き通信を遮断した状態でDjango test suiteを実行する。"""
        with block_external_network():
            return super().run_tests(test_labels, **kwargs)


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
        raise ExternalNetworkBlockedError(f"offline test blocked connection to {address!r}")

    def guarded_connect_ex(sock, address):
        if _is_allowed_address(address):
            return original_connect_ex(sock, address)
        raise ExternalNetworkBlockedError(f"offline test blocked connection to {address!r}")

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host is None or _is_loopback_host(host):
            return original_getaddrinfo(host, *args, **kwargs)
        raise ExternalNetworkBlockedError(f"offline test blocked DNS lookup for {host!r}")

    def guarded_gethostbyaddr(host):
        if _is_loopback_host(host):
            return original_gethostbyaddr(host)
        raise ExternalNetworkBlockedError(f"offline test blocked reverse DNS lookup for {host!r}")

    def guarded_gethostbyname(host):
        if _is_loopback_host(host):
            return original_gethostbyname(host)
        raise ExternalNetworkBlockedError(f"offline test blocked DNS lookup for {host!r}")

    def guarded_gethostbyname_ex(host):
        if _is_loopback_host(host):
            return original_gethostbyname_ex(host)
        raise ExternalNetworkBlockedError(f"offline test blocked DNS lookup for {host!r}")

    def guarded_getnameinfo(address, flags):
        if _is_allowed_address(address):
            return original_getnameinfo(address, flags)
        raise ExternalNetworkBlockedError(f"offline test blocked reverse DNS lookup for {address!r}")

    def guarded_sendto(sock, data, *args):
        if args and _is_allowed_address(args[-1]):
            return original_sendto(sock, data, *args)
        if not args:
            return original_sendto(sock, data, *args)
        raise ExternalNetworkBlockedError(f"offline test blocked UDP send to {args[-1]!r}")

    def guarded_sendmsg(sock, buffers, *args):
        address = args[2] if len(args) >= 3 else None
        if address is None or _is_allowed_address(address):
            return original_sendmsg(sock, buffers, *args)
        raise ExternalNetworkBlockedError(f"offline test blocked message send to {address!r}")

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
