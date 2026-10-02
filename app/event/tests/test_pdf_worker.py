"""PDF workerの資源制限、上限、失敗時契約を確認する."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase
from PIL import Image
from pypdf import PdfWriter

from event.pdf_processing import (
    MAX_PDF_TEXT_PAGES,
    PDF_THUMBNAIL_MAX_LONG_EDGE_PX,
    get_pdf_thumbnail_render_scale,
)
from event.services import pdf_worker
from event.services.pdf_worker import PdfWorkerError, run_pdf_worker
from event_pdf_worker import extract_pdf_text as worker_extract_pdf_text

PDF_FIXTURE = Path(__file__).with_name("input_data") / "perplexity.pdf"


class PdfWorkerTests(SimpleTestCase):
    def setUp(self):
        self.input_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        self.input_file.write(b"test input")
        self.input_file.close()
        self.addCleanup(lambda: os.path.exists(self.input_file.name) and os.unlink(self.input_file.name))

    @staticmethod
    def _successful_process(argv, *, result=b"worker-result"):
        output_path = Path(argv[argv.index("--output") + 1])
        output_path.write_bytes(result)

        class Process:
            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        return Process()

    def test_worker_is_invoked_with_minimal_environment_and_bounded_output_path(self):
        with (
            patch.dict(os.environ, {"PDF_TEST_SECRET_MARKER": "must-not-be-forwarded"}),
            patch.object(
                pdf_worker.subprocess,
                "Popen",
                side_effect=lambda argv, **_kwargs: self._successful_process(argv),
            ) as mock_popen,
            patch.object(pdf_worker, "_record_result") as mock_record,
        ):
            result = run_pdf_worker("text", self.input_file.name, max_chars=12)

        self.assertEqual(result, b"worker-result")
        argv = mock_popen.call_args.args[0]
        kwargs = mock_popen.call_args.kwargs
        self.assertIn("-I", argv)
        self.assertEqual(kwargs["env"], {})
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertTrue(kwargs["close_fds"])
        self.assertNotIn("must-not-be-forwarded", repr(mock_popen.call_args))
        extra = mock_record.call_args.kwargs
        self.assertEqual(mock_record.call_args.args[:3], ("text", "success", None))
        self.assertEqual(set(extra), {"duration_ms", "queue_wait_ms"})

    def test_embedded_uwsgi_falls_back_to_trusted_base_python(self):
        with (
            patch.object(pdf_worker.sys, "_base_executable", "/trusted/python3.12"),
            patch.object(pdf_worker.sys, "executable", "/usr/local/bin/uwsgi"),
            patch("event.services.pdf_worker.os.path.isfile", return_value=True),
            patch("event.services.pdf_worker.os.access", return_value=True),
        ):
            self.assertEqual(pdf_worker._resolve_python_executable(), "/trusted/python3.12")

    def test_venv_python_is_preferred_over_base_python(self):
        with (
            patch.object(pdf_worker.sys, "_base_executable", "/usr/local/bin/python3.12"),
            patch.object(pdf_worker.sys, "executable", "/app/.venv/bin/python"),
            patch("event.services.pdf_worker.os.path.isfile", return_value=True),
            patch("event.services.pdf_worker.os.access", return_value=True),
        ):
            self.assertEqual(pdf_worker._resolve_python_executable(), "/app/.venv/bin/python")

    def test_worker_rejects_result_over_limit_without_reading_it(self):
        with (
            patch.object(pdf_worker, "PDF_WORKER_MAX_OUTPUT_BYTES", 4),
            patch.object(
                pdf_worker.subprocess,
                "Popen",
                side_effect=lambda argv, **_kwargs: self._successful_process(argv, result=b"12345"),
            ),
            patch.object(pdf_worker, "_record_result") as mock_record,
        ):
            with self.assertRaises(PdfWorkerError) as raised:
                run_pdf_worker("text", self.input_file.name, max_chars=12)

        self.assertEqual(raised.exception.reason, "result_too_large")
        self.assertEqual(mock_record.call_args.args[:3], ("text", "failed", "result_too_large"))

    def test_wall_timeout_kills_and_reaps_child(self):
        class TimedOutProcess:
            def __init__(self):
                self.returncode = None
                self.kill_called = False
                self.wait_calls = 0

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                self.wait_calls += 1
                if self.returncode is None:
                    raise subprocess.TimeoutExpired("worker", timeout)
                return self.returncode

            def kill(self):
                self.kill_called = True
                self.returncode = -9

        process = TimedOutProcess()
        with (
            patch.object(pdf_worker.subprocess, "Popen", return_value=process),
            patch.object(pdf_worker, "_record_result") as mock_record,
        ):
            with self.assertRaises(PdfWorkerError) as raised:
                run_pdf_worker("text", self.input_file.name, max_chars=12)

        self.assertEqual(raised.exception.reason, "wall_timeout")
        self.assertTrue(process.kill_called)
        self.assertEqual(process.wait_calls, 2)
        self.assertEqual(mock_record.call_args.args[:3], ("text", "failed", "wall_timeout"))

    def test_kill_race_still_reaps_child(self):
        class ExitedDuringKillProcess:
            def __init__(self):
                self.wait_called = False

            def poll(self):
                return None

            def kill(self):
                raise ProcessLookupError

            def wait(self):
                self.wait_called = True
                return -signal.SIGKILL

        process = ExitedDuringKillProcess()
        pdf_worker._kill_and_reap(process)
        self.assertTrue(process.wait_called)

    def test_hard_cpu_limit_signal_is_reported_as_resource_limit(self):
        with (
            patch.object(pdf_worker, "_resolve_python_executable", return_value="/trusted/python"),
            patch.object(pdf_worker, "_run_child", return_value=-signal.SIGKILL),
            patch.object(pdf_worker, "_record_result") as mock_record,
        ):
            with self.assertRaises(PdfWorkerError) as raised:
                run_pdf_worker("text", self.input_file.name, max_chars=12)

        self.assertEqual(raised.exception.reason, "resource_limit")
        self.assertEqual(mock_record.call_args.args[:3], ("text", "failed", "resource_limit"))

    def test_only_one_child_runs_at_a_time_within_this_process(self):
        first_started = threading.Event()
        allow_first_to_finish = threading.Event()
        popen_calls = []

        class BlockingProcess:
            def __init__(self, argv):
                self.returncode = None
                self.output_path = Path(argv[argv.index("--output") + 1])
                self.output_path.write_bytes(b"serialized")
                first_started.set()

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                if not allow_first_to_finish.wait(timeout):
                    raise subprocess.TimeoutExpired("worker", timeout)
                self.returncode = 0
                return 0

            def kill(self):
                self.returncode = -9

        def start_process(argv, **_kwargs):
            popen_calls.append(argv)
            return BlockingProcess(argv)

        with (
            patch.object(pdf_worker, "_pdf_worker_semaphore", threading.BoundedSemaphore(1)),
            patch.object(pdf_worker, "PDF_WORKER_QUEUE_TIMEOUT_SECONDS", 0.05),
            patch.object(pdf_worker, "PDF_WORKER_WALL_TIMEOUT_SECONDS", 2),
            patch.object(pdf_worker.subprocess, "Popen", side_effect=start_process),
            patch.object(pdf_worker, "_record_result"),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            first = pool.submit(run_pdf_worker, "text", self.input_file.name, max_chars=12)
            self.assertTrue(first_started.wait(2))
            second = pool.submit(run_pdf_worker, "text", self.input_file.name, max_chars=12)
            with self.assertRaises(PdfWorkerError) as raised:
                second.result(timeout=2)
            allow_first_to_finish.set()
            self.assertEqual(first.result(timeout=2), b"serialized")

        self.assertEqual(raised.exception.reason, "queue_timeout")
        self.assertEqual(len(popen_calls), 1)

    def test_worker_text_preserves_page_and_character_limits(self):
        class Page:
            def __init__(self, index):
                self.index = index

            def extract_text(self):
                return f"page-{self.index}"

        pages = [Page(index) for index in range(MAX_PDF_TEXT_PAGES + 5)]
        fake_reader = type("Reader", (), {"pages": pages})()
        text = worker_extract_pdf_text(
            "/tmp/unused.pdf",
            max_chars=18,
            max_pages=5,
            reader_factory=lambda _path: fake_reader,
        )

        self.assertEqual(text, "page-0\npage-1\npage")
        self.assertNotIn("page-5", text)

    def test_normal_pdf_fixture_extracts_text_in_limited_child(self):
        from event.services.content_generation_service import _extract_pdf_text

        text = _extract_pdf_text(str(PDF_FIXTURE), max_chars=240)

        self.assertGreater(len(text), 100)
        self.assertLessEqual(len(text), 240)

    def test_normal_pdf_fixture_renders_bounded_thumbnail_in_limited_child(self):
        thumbnail_bytes = run_pdf_worker("thumbnail", str(PDF_FIXTURE))

        with Image.open(BytesIO(thumbnail_bytes)) as thumbnail:
            self.assertEqual(thumbnail.format, "JPEG")
            self.assertLessEqual(max(thumbnail.size), PDF_THUMBNAIL_MAX_LONG_EDGE_PX)
            self.assertAlmostEqual(thumbnail.width / thumbnail.height, 16 / 9, delta=0.03)

    def test_small_synthetic_pdf_works_in_both_limited_operations(self):
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        synthetic_pdf = BytesIO()
        writer.write(synthetic_pdf)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as pdf_file:
            pdf_file.write(synthetic_pdf.getvalue())
        self.addCleanup(lambda: os.path.exists(pdf_file.name) and os.unlink(pdf_file.name))

        self.assertEqual(run_pdf_worker("text", pdf_file.name, max_chars=100), b"")
        thumbnail_bytes = run_pdf_worker("thumbnail", pdf_file.name)
        with Image.open(BytesIO(thumbnail_bytes)) as thumbnail:
            self.assertEqual(thumbnail.format, "JPEG")
            self.assertLessEqual(max(thumbnail.size), PDF_THUMBNAIL_MAX_LONG_EDGE_PX)

    def test_page_render_scale_keeps_existing_long_edge_cap(self):
        class Page:
            def get_size(self):
                return (4000, 2000)

        scale = get_pdf_thumbnail_render_scale(Page())
        self.assertAlmostEqual(4000 * scale, PDF_THUMBNAIL_MAX_LONG_EDGE_PX)
