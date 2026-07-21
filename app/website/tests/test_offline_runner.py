"""外向き通信を遮断するtest runner境界の回帰テスト。"""

import errno
import socket
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from django.test import SimpleTestCase

from website.tests.offline_runner import ExternalNetworkBlockedError

# socket.herror exposes h_errno=2 but not the legacy TRY_AGAIN constant.
H_ERRNO_TRY_AGAIN = 2


class OfflineNetworkRunnerTest(SimpleTestCase):
    """CIのrunnerが外向き通信を拒否しloopbackを維持することを確認する。"""

    def assert_external_operation_is_blocked(
        self, operation: Callable[[], object], accepted_os_errnos: frozenset[int]
    ) -> None:
        """Python遮断またはnetwork namespace遮断を外向き通信の失敗として受け入れる。"""
        try:
            operation()
        except ExternalNetworkBlockedError:
            return
        except OSError as exc:
            if exc.errno in accepted_os_errnos:
                return
            raise
        self.fail("external network operation unexpectedly succeeded")

    def test_external_dns_lookup_is_blocked(self):
        self.assert_external_operation_is_blocked(
            lambda: socket.getaddrinfo("example.com", 443),
            frozenset((socket.EAI_AGAIN, H_ERRNO_TRY_AGAIN)),
        )

    def test_external_ip_connection_is_blocked(self):
        with socket.socket() as client:
            self.assert_external_operation_is_blocked(
                lambda: client.connect(("203.0.113.1", 443)),
                frozenset((errno.ENETUNREACH,)),
            )

    def test_external_udp_send_is_blocked(self):
        with socket.socket(type=socket.SOCK_DGRAM) as client:
            self.assert_external_operation_is_blocked(
                lambda: client.sendto(b"blocked", ("203.0.113.1", 53)),
                frozenset((errno.ENETUNREACH,)),
            )

    def test_external_sendmsg_is_blocked(self):
        if not hasattr(socket.socket, "sendmsg"):
            self.skipTest("socket.sendmsg is unavailable")
        with socket.socket(type=socket.SOCK_DGRAM) as client:
            self.assert_external_operation_is_blocked(
                lambda: client.sendmsg([b"blocked"], [], 0, ("203.0.113.1", 53)),
                frozenset((errno.ENETUNREACH,)),
            )

    def test_alternate_external_dns_lookups_are_blocked(self):
        lookups = (
            lambda: socket.gethostbyname("example.com"),
            lambda: socket.gethostbyname_ex("example.com"),
            lambda: socket.gethostbyaddr("203.0.113.1"),
            lambda: socket.getnameinfo(("203.0.113.1", 443), 0),
        )
        for lookup in lookups:
            with self.subTest(lookup=lookup):
                self.assert_external_operation_is_blocked(
                    lookup,
                    frozenset((socket.EAI_AGAIN, H_ERRNO_TRY_AGAIN)),
                )

    def test_loopback_connection_is_allowed(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        accepted = threading.Event()

        def accept_once():
            connection, _ = server.accept()
            connection.close()
            accepted.set()

        thread = threading.Thread(target=accept_once)
        thread.start()
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                pass
            self.assertTrue(accepted.wait(timeout=1))
        finally:
            server.close()
            thread.join(timeout=1)

    def test_ipv4_loopback_udp_is_allowed(self):
        with socket.socket(type=socket.SOCK_DGRAM) as server:
            server.bind(("127.0.0.1", 0))
            with socket.socket(type=socket.SOCK_DGRAM) as client:
                client.sendto(b"allowed", server.getsockname())
            self.assertEqual(server.recvfrom(32)[0], b"allowed")

    def test_ipv6_loopback_udp_is_allowed(self):
        if not socket.has_ipv6:
            self.skipTest("IPv6 is unavailable")
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as server:
            try:
                server.bind(("::1", 0))
            except OSError as exc:
                self.skipTest(f"IPv6 loopback is unavailable: {exc}")
            with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as client:
                client.sendto(b"allowed", server.getsockname())
            self.assertEqual(server.recvfrom(32)[0], b"allowed")

    def test_unix_datagram_socket_is_allowed(self):
        if not hasattr(socket, "AF_UNIX"):
            self.skipTest("Unix sockets are unavailable")
        with tempfile.TemporaryDirectory() as temp_dir:
            socket_path = str(Path(temp_dir) / "offline.sock")
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
                server.bind(socket_path)
                with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
                    client.sendto(b"allowed", socket_path)
                self.assertEqual(server.recvfrom(32)[0], b"allowed")
