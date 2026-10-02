"""DjangoからPDFを資源制限付きworkerへ渡す.

これはCPU/メモリ/時間の制限であり、ネットワークや権限を隔離するsandboxではない。
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

PDF_WORKER_QUEUE_TIMEOUT_SECONDS = 30
PDF_WORKER_WALL_TIMEOUT_SECONDS = 30
PDF_WORKER_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
PDF_WORKER_CONCURRENCY = 1
PDF_WORKER_SCRIPT = Path(__file__).resolve().parents[2] / "event_pdf_worker.py"
_SYSTEM_PYTHON_CANDIDATES = (
    "/usr/local/bin/python3.12",
    "/usr/local/bin/python3",
    "/usr/bin/python3",
)

# This semaphore is per Python process. The deployed uWSGI configuration uses
# one process with ten threads; it is not a cross-process or per-instance lock.
_pdf_worker_semaphore = threading.BoundedSemaphore(PDF_WORKER_CONCURRENCY)


class PdfWorkerError(Exception):
    """Sanitized worker failure; never carries a file path or parser message."""

    def __init__(self, operation: str, reason: str):
        super().__init__(f"PDF worker {reason}")
        self.operation = operation
        self.reason = reason


def _record_result(
    operation: str,
    outcome: str,
    reason: str | None,
    *,
    duration_ms: int,
    queue_wait_ms: int,
) -> None:
    logger.log(
        logging.INFO if outcome == "success" else logging.WARNING,
        "pdf_worker_result",
        extra={
            "operation": operation,
            "outcome": outcome,
            "reason": reason,
            "duration_ms": duration_ms,
            "queue_wait_ms": queue_wait_ms,
        },
    )


def _kill_and_reap(process: subprocess.Popen) -> None:
    if process.poll() is None:
        try:
            process.kill()
        except ProcessLookupError:
            # The child may exit between poll() and kill(); still reap it.
            pass
    process.wait()


def _resolve_python_executable() -> str:
    """Resolve a trusted Python binary even when called from embedded uWSGI."""
    candidates = (
        sys.executable,
        getattr(sys, "_base_executable", None),
        *_SYSTEM_PYTHON_CANDIDATES,
    )
    for candidate in candidates:
        if not isinstance(candidate, str) or not os.path.isabs(candidate):
            continue
        executable_name = os.path.basename(candidate).lower()
        if not executable_name.startswith(("python", "pypy")):
            continue
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise PdfWorkerError("unknown", "worker_error")


def _run_child(argv: list[str]) -> int:
    process = subprocess.Popen(
        argv,
        cwd="/",
        env={},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    try:
        try:
            return process.wait(timeout=PDF_WORKER_WALL_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as error:
            _kill_and_reap(process)
            raise PdfWorkerError("unknown", "wall_timeout") from error
    finally:
        if process.poll() is None:
            _kill_and_reap(process)


def run_pdf_worker(operation: str, input_path: str, *, max_chars: int | None = None) -> bytes:
    """Run one worker and return a bounded result; failures become sanitized errors."""
    if operation not in {"text", "thumbnail"}:
        raise ValueError("unsupported PDF operation")
    if operation == "text" and (max_chars is None or max_chars < 0):
        raise ValueError("text extraction requires a non-negative character budget")

    started = time.monotonic()
    if not _pdf_worker_semaphore.acquire(timeout=PDF_WORKER_QUEUE_TIMEOUT_SECONDS):
        duration_ms = int((time.monotonic() - started) * 1000)
        _record_result(
            operation,
            "failed",
            "queue_timeout",
            duration_ms=duration_ms,
            queue_wait_ms=duration_ms,
        )
        raise PdfWorkerError(operation, "queue_timeout")

    queue_wait_ms = int((time.monotonic() - started) * 1000)
    reason = None
    result = None
    try:
        if not os.path.isfile(input_path):
            reason = "worker_error"
            raise PdfWorkerError(operation, reason)

        with tempfile.TemporaryDirectory(prefix="pdf-worker-") as worker_dir:
            output_path = Path(worker_dir) / "result"
            argv = [
                _resolve_python_executable(),
                "-I",
                str(PDF_WORKER_SCRIPT),
                "--operation",
                operation,
                "--input",
                os.path.abspath(input_path),
                "--output",
                str(output_path),
            ]
            if operation == "text":
                argv.extend(("--max-chars", str(max_chars)))

            try:
                return_code = _run_child(argv)
            except PdfWorkerError as error:
                reason = error.reason
                raise PdfWorkerError(operation, reason) from error
            except (OSError, subprocess.SubprocessError) as error:
                reason = "worker_error"
                raise PdfWorkerError(operation, reason) from error

            if return_code in {3, -signal.SIGXCPU, -signal.SIGXFSZ, -signal.SIGKILL}:
                reason = "resource_limit"
                raise PdfWorkerError(operation, reason)
            if return_code != 0:
                reason = "worker_error"
                raise PdfWorkerError(operation, reason)

            try:
                output_size = output_path.stat().st_size
            except OSError as error:
                reason = "worker_error"
                raise PdfWorkerError(operation, reason) from error
            if output_size > PDF_WORKER_MAX_OUTPUT_BYTES:
                reason = "result_too_large"
                raise PdfWorkerError(operation, reason)

            with output_path.open("rb") as output_file:
                result = output_file.read(PDF_WORKER_MAX_OUTPUT_BYTES + 1)
            if len(result) > PDF_WORKER_MAX_OUTPUT_BYTES:
                reason = "result_too_large"
                raise PdfWorkerError(operation, reason)
            if operation == "thumbnail" and not result:
                reason = "worker_error"
                raise PdfWorkerError(operation, reason)
    except PdfWorkerError as error:
        reason = error.reason
        raise
    except Exception as error:
        reason = "worker_error"
        raise PdfWorkerError(operation, reason) from error
    finally:
        _pdf_worker_semaphore.release()
        duration_ms = int((time.monotonic() - started) * 1000)
        _record_result(
            operation,
            "failed" if reason else "success",
            reason,
            duration_ms=duration_ms,
            queue_wait_ms=queue_wait_ms,
        )

    return result
