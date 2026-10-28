"""One short-lived bounded parser process; bytes only, no vault/DB paths.

The launcher is internal trusted code. It is not a shell tool and accepts no
command, URL, plugin, environment override or installation instruction.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import sysconfig
import threading
import time
from pathlib import Path

from .attachment_metadata import PDF_SCOPE, SCOPE, failure, validate

WALL_SECONDS = 15
QUEUE_SECONDS = 5
STDOUT_LIMIT = 512 * 1024
STDERR_LIMIT = 16 * 1024
_GATE = threading.BoundedSemaphore(1)
_WORKER = Path(__file__).with_name("_attachment_parser_worker.py")


def _environment():
    # Python isolated mode also ignores Python env flags. No service secrets.
    return {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "SYSTEMDRIVE") if key in os.environ}


def _command(kind):
    if kind not in {"docx", "pdf", "xlsx"}:
        raise ValueError("Invalid fixed parser kind")
    # Windows venv python.exe is a redirector. Kill/wait must target the actual
    # parser PID, so launch the base interpreter and add only trusted venv libs
    # after its resource gate. Never process site .pth or inherit PYTHONPATH.
    executable = getattr(sys, "_base_executable", sys.executable) if os.name == "nt" else sys.executable
    # -I ignores Python environment flags; read-only parsing must not cache in the installation.
    command = [executable, "-I", "-B", "-S", str(_WORKER)]
    if sys.platform == "win32":
        from ._windows_process_host import parent_arguments

        command.extend(parent_arguments()[1:])
    elif sys.platform == "linux":
        command.append(str(os.getpid()))
    paths = sysconfig.get_paths()
    command.extend([kind, paths["purelib"], paths["platlib"]])
    return command


def _run(data, cancelled, scope=SCOPE, kind="docx"):
    def failed(reason, **kwargs):
        return failure(reason, scope=scope, **kwargs)

    if sys.platform not in {"linux", "win32"}:
        return failed("limits_unavailable", unavailable=True)
    deadline = time.monotonic() + QUEUE_SECONDS
    acquired = False
    while not cancelled.is_set() and time.monotonic() < deadline:
        if _GATE.acquire(timeout=min(.05, max(0, deadline - time.monotonic()))):
            acquired = True
            break
    if not acquired:
        return failed("queue_timeout", unavailable=True)
    process, readers = None, []
    output, errors = bytearray(), bytearray()
    overflow = threading.Event()
    try:
        if cancelled.is_set():
            return failed("parser_crash")
        try:
            process = subprocess.Popen(_command(kind), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, env=_environment(), close_fds=True,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except (OSError, ValueError, RuntimeError):
            return failed("limits_unavailable", unavailable=True)
        deadline = time.monotonic() + WALL_SECONDS

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
                process.stdin.write(data)
                process.stdin.flush()
            except (OSError, ValueError):
                pass
            finally:
                process.stdin.close()

        for stream, target, limit in ((process.stdout, output, STDOUT_LIMIT),
                                      (process.stderr, errors, STDERR_LIMIT)):
            reader = threading.Thread(target=read, args=(stream, target, limit), daemon=True)
            reader.start()
            readers.append(reader)
        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        readers.append(writer)
        reason = None
        while process.poll() is None:
            if cancelled.is_set():
                reason = "parser_crash"
            elif overflow.is_set():
                reason = "output_limit"
            elif time.monotonic() >= deadline:
                reason = "parser_timeout"
            if reason:
                process.kill()
                break
            cancelled.wait(.02)
        process.wait()  # Actual child reaped before releasing the only parser slot.
        for reader in readers:
            reader.join()
        if reason or overflow.is_set():
            return failed(reason or "output_limit")
        if process.returncode == 126:
            return failed("limits_unavailable", unavailable=True)
        if process.returncode:
            return failed("parser_crash")
        try:
            result = json.loads(output)
            return validate(result, expected_scope=scope,
                            expected_kind="xlsx" if kind == "xlsx" else None)
        except (ValueError, UnicodeError, TypeError):
            return failed("invalid_parser_output")
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        for reader in readers:
            reader.join()
        _GATE.release()


async def inspect_docx(data):
    return await _inspect(data, SCOPE, "docx")


async def inspect_pdf(data):
    return await _inspect(data, PDF_SCOPE, "pdf")


async def inspect_xlsx(data):
    return await _inspect(data, PDF_SCOPE, "xlsx")


async def _inspect(data, scope, kind):
    cancelled = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(_run, data, cancelled, scope, kind))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancelled.set()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        raise
