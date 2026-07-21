"""外向き通信を遮断するtest runner境界の回帰テスト。"""

import socket
import threading

from django.test import SimpleTestCase

from website.tests.offline_runner import ExternalNetworkBlockedError


class OfflineNetworkRunnerTest(SimpleTestCase):
    """CIのrunnerが外向き通信を拒否しloopbackを維持することを確認する。"""

    def test_external_dns_lookup_is_blocked(self):
        with self.assertRaises(ExternalNetworkBlockedError):
            socket.getaddrinfo("example.com", 443)

    def test_external_ip_connection_is_blocked(self):
        with socket.socket() as client:
            with self.assertRaises(ExternalNetworkBlockedError):
                client.connect(("203.0.113.1", 443))

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
