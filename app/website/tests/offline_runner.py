"""通常テストから外向きネットワーク接続を遮断するtest runner。"""

import ipaddress
import socket
from contextlib import ExitStack, contextmanager
from collections.abc import Iterator
from unittest.mock import patch

from django.test.runner import DiscoverRunner


class ExternalNetworkBlockedError(ConnectionError):
    """許可されていない外向き接続を検出したことを表す。"""


def _is_loopback_host(host: object) -> bool:
    if not isinstance(host, str):
        return False
    normalized = host.rstrip(".").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


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

    def guarded_connect(sock, address):
        if isinstance(address, (str, bytes)) or _is_loopback_host(address[0]):
            return original_connect(sock, address)
        raise ExternalNetworkBlockedError(f"offline test blocked connection to {address[0]!r}")

    def guarded_connect_ex(sock, address):
        if isinstance(address, (str, bytes)) or _is_loopback_host(address[0]):
            return original_connect_ex(sock, address)
        raise ExternalNetworkBlockedError(f"offline test blocked connection to {address[0]!r}")

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host is None or _is_loopback_host(host):
            return original_getaddrinfo(host, *args, **kwargs)
        raise ExternalNetworkBlockedError(f"offline test blocked DNS lookup for {host!r}")

    with ExitStack() as stack:
        stack.enter_context(patch("socket.socket.connect", guarded_connect))
        stack.enter_context(patch("socket.socket.connect_ex", guarded_connect_ex))
        stack.enter_context(patch("socket.getaddrinfo", guarded_getaddrinfo))
        yield
