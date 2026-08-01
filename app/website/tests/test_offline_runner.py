"""外向き通信を遮断するtest runner境界の回帰テスト。"""

import contextlib
import errno
import io
import socket
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase
from django.test.runner import DiscoverRunner

from website.tests.offline_runner import (
    ExternalNetworkBlockedError,
    OfflineNetworkDiscoverRunner,
    blocked_network_recorder,
)

# socket.herror exposes h_errno=2 but not the legacy TRY_AGAIN constant.
H_ERRNO_TRY_AGAIN = 2


class OfflineNetworkRunnerTest(SimpleTestCase):
    """CIのrunnerが外向き通信を拒否しloopbackを維持することを確認する。"""

    def assert_external_operation_is_blocked(
        self,
        operation: Callable[[], object],
        accepted_os_errors: tuple[tuple[type[OSError], frozenset[int]], ...],
    ) -> None:
        """Python遮断またはnetwork namespace遮断を外向き通信の失敗として受け入れる。

        遮断はこのテストの期待動作なので、suite の遮断件数には数えない。
        """
        try:
            with blocked_network_recorder.suppressed():
                operation()
        except ExternalNetworkBlockedError:
            return
        except OSError as exc:
            for exception_type, accepted_errnos in accepted_os_errors:
                if isinstance(exc, exception_type) and exc.errno in accepted_errnos:
                    return
            raise
        self.fail("external network operation unexpectedly succeeded")

    def test_external_dns_lookup_is_blocked(self):
        self.assert_external_operation_is_blocked(
            lambda: socket.getaddrinfo("example.com", 443),
            (
                (socket.gaierror, frozenset((socket.EAI_AGAIN,))),
                (socket.herror, frozenset((H_ERRNO_TRY_AGAIN,))),
            ),
        )

    def test_external_ip_connection_is_blocked(self):
        with socket.socket() as client:
            self.assert_external_operation_is_blocked(
                lambda: client.connect(("203.0.113.1", 443)),
                ((OSError, frozenset((errno.ENETUNREACH,))),),
            )

    def test_external_udp_send_is_blocked(self):
        with socket.socket(type=socket.SOCK_DGRAM) as client:
            self.assert_external_operation_is_blocked(
                lambda: client.sendto(b"blocked", ("203.0.113.1", 53)),
                ((OSError, frozenset((errno.ENETUNREACH,))),),
            )

    def test_external_sendmsg_is_blocked(self):
        if not hasattr(socket.socket, "sendmsg"):
            self.skipTest("socket.sendmsg is unavailable")
        with socket.socket(type=socket.SOCK_DGRAM) as client:
            self.assert_external_operation_is_blocked(
                lambda: client.sendmsg([b"blocked"], [], 0, ("203.0.113.1", 53)),
                ((OSError, frozenset((errno.ENETUNREACH,))),),
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
                    (
                        (socket.gaierror, frozenset((socket.EAI_AGAIN,))),
                        (socket.herror, frozenset((H_ERRNO_TRY_AGAIN,))),
                    ),
                )

    def test_unrelated_enoent_is_not_treated_as_dns_blocking(self):
        """同じerrno値でもDNS例外でないFileNotFoundErrorは再送出する。"""
        def raise_enoent():
            raise FileNotFoundError(errno.ENOENT, "missing")

        with self.assertRaises(FileNotFoundError):
            self.assert_external_operation_is_blocked(
                raise_enoent,
                (
                    (socket.gaierror, frozenset((socket.EAI_AGAIN,))),
                    (socket.herror, frozenset((H_ERRNO_TRY_AGAIN,))),
                ),
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


class BlockedNetworkDetectionTest(SimpleTestCase):
    """遮断イベントが suite の終了コードとレポートに反映されることを確認する。"""

    def setUp(self):
        self._outer_events = blocked_network_recorder.snapshot()
        self.addCleanup(self._restore_outer_events)

    def _restore_outer_events(self):
        """外側の suite が集めていた記録を元に戻す。"""
        blocked_network_recorder.restore(self._outer_events)

    @staticmethod
    def _swallowing_suite(_self, _test_labels, **_kwargs):
        """アプリと同様に接続エラーを握りつぶす「緑の」suiteを模擬する。"""
        try:
            socket.getaddrinfo("discord.com", 443)
        except OSError:
            pass
        return 0

    def _run_suite_with(self, suite_body):
        """OfflineNetworkDiscoverRunner 経由で疑似suiteを実行し、失敗数とstderrを返す。"""
        stderr = io.StringIO()
        runner = OfflineNetworkDiscoverRunner(verbosity=0, interactive=False)
        with patch.object(DiscoverRunner, "run_tests", suite_body), \
                contextlib.redirect_stderr(stderr):
            failures = runner.run_tests([])
        return failures, stderr.getvalue()

    def test_swallowed_external_call_fails_the_suite(self):
        """アプリ側が例外を握りつぶしても suite が失敗になる。"""
        failures, report = self._run_suite_with(self._swallowing_suite)

        self.assertEqual(failures, 1)
        self.assertIn("外向き通信が 1 件遮断されました", report)
        self.assertIn("discord.com", report)
        self.assertIn("test_offline_runner.py", report)

    def test_multiple_blocks_add_one_failure_and_report_the_count(self):
        """遮断が複数件でも失敗数への加算は1件（件数はレポートで示す）。"""
        def repeatedly_swallowing_suite(_self, _labels, **_kwargs):
            for _ in range(3):
                try:
                    socket.getaddrinfo("discord.com", 443)
                except OSError:
                    pass
            return 0

        failures, report = self._run_suite_with(repeatedly_swallowing_suite)

        self.assertEqual(failures, 1)
        self.assertIn("外向き通信が 3 件遮断されました", report)

    def test_report_does_not_include_source_lines(self):
        """レポートにソース行を出さない（webhook URL 等のリテラル漏洩防止）。"""
        secret_marker = "webhook-token-must-not-appear"

        def suite_with_literal(_self, _labels, **_kwargs):
            try:
                # 遮断が起きる行そのものに秘密値リテラルを置く（旧実装ならこの行が出力された）。
                socket.getaddrinfo("discord.example.invalid", 443, 0, 0, 0, 0)  # webhook-token-must-not-appear
            except OSError:
                pass
            return 0

        _, report = self._run_suite_with(suite_with_literal)

        self.assertIn("test_offline_runner.py", report)
        self.assertNotIn(secret_marker, report)

    def test_clean_suite_keeps_original_failure_count(self):
        """遮断が起きなければ元の失敗数をそのまま返す。"""
        failures, report = self._run_suite_with(lambda _self, _labels, **_kw: 2)

        self.assertEqual(failures, 2)
        self.assertEqual(report, "")

    def test_suppressed_block_is_not_counted(self):
        """意図的に遮断を起こすテストは件数に数えない。"""
        def suppressed_suite(_self, _labels, **_kwargs):
            with blocked_network_recorder.suppressed():
                try:
                    socket.getaddrinfo("discord.com", 443)
                except OSError:
                    pass
            return 0

        failures, report = self._run_suite_with(suppressed_suite)

        self.assertEqual(failures, 0)
        self.assertEqual(report, "")
