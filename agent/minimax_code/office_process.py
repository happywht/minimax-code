"""One bounded, fixed XLSX worker. Cancellation reaps it before releasing its slot."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import subprocess
import sys
import sysconfig
import threading
import time
from pathlib import Path

from .office_xlsx import MAX_INPUT, MAX_OUTPUT, XlsxSpec, canonical, digest

WALL_SECONDS = 15
QUEUE_SECONDS = 5
STDOUT_LIMIT = 3 * 1024 * 1024
STDERR_LIMIT = 16 * 1024
_GATE = threading.BoundedSemaphore(1)


class OfficeProcessError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(f"Office generation unavailable: {reason}")


def _command():
    executable = getattr(sys, "_base_executable", sys.executable) if os.name == "nt" else sys.executable
    command = [executable, "-I", "-B", "-S",
               str(Path(__file__).with_name("_office_xlsx_worker.py"))]
    if sys.platform == "win32":
        from ._windows_process_host import parent_arguments

        command.extend(parent_arguments()[1:])
    elif sys.platform == "linux":
        command.append(str(os.getpid()))
    paths = sysconfig.get_paths()
    return [*command, "xlsx_generate", paths["purelib"], paths["platlib"]]


def _run(spec, cancelled):
    from .office_metadata import validate_report

    if sys.platform not in {"linux", "win32"}:
        raise OfficeProcessError("limits_unavailable")
    raw = canonical(spec.model_dump())
    if len(raw) > MAX_INPUT:
        raise OfficeProcessError("input_limit")
    acquired = False
    deadline = time.monotonic() + QUEUE_SECONDS
    while not cancelled.is_set() and time.monotonic() < deadline:
        if _GATE.acquire(timeout=min(.05, max(0, deadline - time.monotonic()))):
            acquired = True
            break
    if not acquired:
        raise OfficeProcessError("queue_timeout")
    process, threads = None, []
    output, errors = bytearray(), bytearray()
    overflow = threading.Event()
    try:
        if cancelled.is_set():
            raise OfficeProcessError("cancelled")
        environment = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "SYSTEMDRIVE")
                       if key in os.environ}
        try:
            process = subprocess.Popen(_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, env=environment, close_fds=True,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except (OSError, ValueError, RuntimeError) as error:
            raise OfficeProcessError("limits_unavailable") from error

        def read(stream, target, limit):
            try:
                while chunk := stream.read(4096):
                    remaining = max(0, limit - len(target))
                    target.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        overflow.set()
            finally:
                stream.close()

        def write():
            try:
                process.stdin.write(raw)
                process.stdin.flush()
            except (OSError, ValueError):
                pass
            finally:
                process.stdin.close()

        for stream, target, limit in ((process.stdout, output, STDOUT_LIMIT),
                                      (process.stderr, errors, STDERR_LIMIT)):
            thread = threading.Thread(target=read, args=(stream, target, limit), daemon=True)
            thread.start()
            threads.append(thread)
        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        threads.append(writer)
        deadline = time.monotonic() + WALL_SECONDS
        while process.poll() is None:
            reason = ("cancelled" if cancelled.is_set() else "output_limit" if overflow.is_set()
                      else "worker_timeout" if time.monotonic() >= deadline else None)
            if reason:
                process.kill()
                process.wait()
                raise OfficeProcessError(reason)
            cancelled.wait(.02)
        process.wait()
        for thread in threads:
            thread.join()
        if overflow.is_set():
            raise OfficeProcessError("output_limit")
        if process.returncode == 126:
            raise OfficeProcessError("limits_unavailable")
        if process.returncode:
            raise OfficeProcessError("worker_crash")
        try:
            result = json.loads(output)
            if result == {"error": "missing_dependency"}:
                raise OfficeProcessError("missing_dependency")
            if result == {"error": "generation_failed"}:
                raise OfficeProcessError("generation_failed")
            if type(result) is not dict or set(result) != {"data_base64", "report"}:
                raise ValueError
            data = base64.b64decode(result["data_base64"], validate=True)
            report = validate_report(result["report"])
            if (not 0 < len(data) <= MAX_OUTPUT or report["size_bytes"] != len(data)
                    or report["file_sha256"] != hashlib.sha256(data).hexdigest()
                    or report["input_sha256"] != digest(spec.model_dump())
                    or report["sheet_name"] != spec.sheet_name
                    or report["data_rows"] != len(spec.rows)
                    or report["column_count"] != len(spec.columns)):
                raise ValueError
            return data, report
        except OfficeProcessError:
            raise
        except Exception as error:
            raise OfficeProcessError("invalid_worker_output") from error
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        for thread in threads:
            thread.join()
        _GATE.release()


async def generate_xlsx(spec: XlsxSpec):
    cancelled = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(_run, spec, cancelled))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancelled.set()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise
