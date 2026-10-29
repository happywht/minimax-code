"""Shared local process boundary. Working directories are not OS sandboxes.

All caller-supplied commands are treated as capable of writing. Read-only
execution therefore rejects arbitrary processes, rather than guessing from
their command strings. Explicit server configuration may inject credentials;
ambient server credentials are never inherited.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import subprocess
import sys
from collections.abc import Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .workspace_ctx import current_root, current_sandbox, env_or_cwd_root, require_directory


class ExecutionDeniedError(PermissionError):
    pass


@dataclass(frozen=True)
class ExecutionPolicy:
    workspace: Path | None = None
    read_only: bool = False


_policy: ContextVar[ExecutionPolicy | None] = ContextVar("execution_policy", default=None)


def is_read_only() -> bool:
    policy = _policy.get()
    return policy is not None and policy.read_only


@contextlib.contextmanager
def execution_scope(policy: ExecutionPolicy) -> Iterator[None]:
    parent = _policy.get()
    if parent is not None:
        # Resolve at nesting time: the outer agent scope may be entered before
        # its session root is installed. A missing inner root inherits this root.
        root = require_directory(parent.workspace or current_root() or env_or_cwd_root())
        target = require_directory(policy.workspace) if policy.workspace is not None else root
        if not target.is_relative_to(root):
            raise ExecutionDeniedError("Nested execution workspace is outside the parent workspace.")
        policy = ExecutionPolicy(workspace=target, read_only=parent.read_only or policy.read_only)
    token = _policy.set(policy)
    try:
        yield
    finally:
        _policy.reset(token)


def require_write_access() -> None:
    policy = _policy.get()
    if policy is not None and policy.read_only:
        raise ExecutionDeniedError("Planning mode is read-only; switch to execute before modifying files or running commands.")


def require_tool_access(name: str) -> None:
    policy = _policy.get()
    if policy is not None and policy.read_only and name not in {
        "read_file", "list_directory", "list_dir", "search_files", "grep", "glob",
        "search_codebase", "summarize_codebase", "find_symbol", "navigate_codebase",
        "list_project_attachments", "read_project_attachment", "read_project_document",
        "search_public_web", "read_public_page", "list_research_sources", "get_research_source",
        "ask_user", "check_subagent", "list_agents",
    }:
        raise ExecutionDeniedError(f"Tool {name!r} is unavailable in read-only planning mode.")


def process_cwd(cwd: str | Path | None) -> str:
    require_write_access()
    return _validated_process_cwd(cwd)


def _validated_process_cwd(cwd: str | Path | None) -> str:
    """Shared validation; trusted engine startup has its own policy capability."""
    if current_sandbox() is not None:
        raise ExecutionDeniedError(
            "Processes cannot run in a file overlay: it does not isolate shell writes. "
            "Use file tools, or run the command in an independent workspace."
        )
    policy = _policy.get()
    root = require_directory(policy.workspace) if policy and policy.workspace else current_root()
    if root is not None:
        root = require_directory(root)
    target = Path(cwd).expanduser() if cwd else (root or env_or_cwd_root())
    if not target.is_absolute():
        target = (root or Path.cwd()) / target
    target = require_directory(target)
    if root is not None and not target.is_relative_to(root):
        raise ExecutionDeniedError("Process working directory is outside the active workspace.")
    return str(target)


_SAFE_ENV = frozenset({
    "PATH", "PATHEXT", "HOME", "USERPROFILE", "HOMEPATH", "HOMEDRIVE",
    "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "SYSTEMROOT",
    "COMSPEC", "PROCESSOR_ARCHITECTURE", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "PROGRAMDATA", "LOCALAPPDATA", "APPDATA", "TERM", "JAVA_HOME", "GOPATH",
    "GOROOT", "CARGO_HOME", "RUSTUP_HOME", "PYTHONUNBUFFERED", "PYTHONIOENCODING",
    "MINIMAX_CODE_DATA_DIR",
})


def process_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key.upper() in _SAFE_ENV}
    env.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    # Only explicit, trusted configuration belongs here. Agent-supplied tool
    # env goes through the terminal tool's stricter filtering first.
    for key, value in (extra or {}).items():
        if not isinstance(key, str) or not key or not isinstance(value, str) or "=" in key or "\0" in key + value:
            raise ValueError("Invalid child environment entry")
        env[key] = value
    return env


def spawn_options() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


async def _spawn(factory, *args, **kwargs) -> asyncio.subprocess.Process:
    kwargs.setdefault("stdin", asyncio.subprocess.DEVNULL)
    starting = asyncio.create_task(factory(*args, **kwargs))
    try:
        return await asyncio.shield(starting)
    except asyncio.CancelledError:
        # Cancellation may arrive after the OS created the process but before
        # asyncio returned its handle. Recover that handle before re-raising.
        try:
            proc = await _finish_owned(starting)
        except Exception:
            pass
        else:
            await _finish_owned(asyncio.create_task(terminate_tree(proc)))
        raise


async def _finish_owned(task: asyncio.Task[Any]) -> Any:
    """Finish a retained cleanup/ownership task despite repeated cancellation.

    Callers handling cancellation re-raise it after this ownership transfer;
    this helper itself deliberately consumes additional cancellation requests.
    """
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def spawn_exec(*argv: str, cwd: str | Path | None = None,
                     env: Mapping[str, str] | None = None, **kwargs: Any) -> asyncio.subprocess.Process:
    resolved_cwd = process_cwd(cwd)
    from .process_context import current_process_target
    if current_process_target() is not None:
        return await _spawn_restricted(argv, cwd=resolved_cwd, env=env, options=kwargs)
    child_env = process_environment(env)
    return await _spawn_managed_exec(*argv, cwd=resolved_cwd, env=child_env, **kwargs)


async def _spawn_restricted(argv, *, cwd, env, options):
    from .app import get_execution_profiles
    from .process_context import current_process_target
    from .process_types import ProcessBoundaryError

    required = current_process_target()
    if required is None or required.owner is None:
        raise ProcessBoundaryError("Restricted process requires its durable prepared run owner")
    catalog = get_execution_profiles()
    if catalog is None or catalog.supervisor is None:
        raise ProcessBoundaryError("Restricted process service is unavailable")
    catalog.assert_compatible(required.target.to_dict(), catalog.installation_id)
    if (set(options) - {"stdin", "stdout", "stderr"}
            or options.get("stdin", asyncio.subprocess.DEVNULL) not in
                (asyncio.subprocess.PIPE, asyncio.subprocess.DEVNULL)
            or any(options.get(name, asyncio.subprocess.PIPE) != asyncio.subprocess.PIPE
                   for name in ("stdout", "stderr"))):
        raise ProcessBoundaryError("Restricted processes require bounded separate pipe streams")
    return await catalog.supervisor.spawn(argv, target=required.target,
        owner=required.owner, cwd=cwd, env=env,
        stdin=options.get("stdin") == asyncio.subprocess.PIPE)


async def _spawn_managed_exec(*argv: str, cwd: str,
                              env: Mapping[str, str], **kwargs: Any) -> asyncio.subprocess.Process:
    """Internal primitive. Public tool callers must go through spawn_exec policy.

    Engine adapters validate their own narrow launch capability before calling
    this primitive; it is never a tool-facing policy override.
    """
    if not argv:
        raise ValueError("A process command is required")
    if sys.platform == "win32":
        from ._windows_process_host import parent_arguments

        # Resolve the binary before wrapping to preserve spawn error semantics.
        binary = shutil.which(argv[0], path=env.get("PATH"))
        if binary is None:
            raise FileNotFoundError(f"command not found: {argv[0]}")
        # A Windows venv redirector waits with inherited pipes open. The host
        # is stdlib-only; launch the real interpreter so it can release its
        # own stdio copies after handing them to the managed command.
        host_python = getattr(sys, "_base_executable", None) or sys.executable
        argv = (host_python, "-I", "-S", str(Path(__file__).with_name("_windows_process_host.py")),
                *parent_arguments(), "--exec", binary, *argv[1:])
    return await _spawn(asyncio.create_subprocess_exec,
        *argv, cwd=cwd, env=env,
        **(spawn_options() | kwargs),
    )


async def spawn_shell(command: str, *, cwd: str | Path | None = None,
                      env: Mapping[str, str] | None = None, **kwargs: Any) -> asyncio.subprocess.Process:
    from .process_context import current_process_target
    if current_process_target() is not None:
        return await spawn_exec("/bin/sh", "-c", command, cwd=cwd, env=env, **kwargs)
    if sys.platform == "win32":
        from ._windows_process_host import parent_arguments

        return await _spawn(asyncio.create_subprocess_exec,
            getattr(sys, "_base_executable", None) or sys.executable,
            "-I", "-S", str(Path(__file__).with_name("_windows_process_host.py")),
            *parent_arguments(), "--shell", command, cwd=process_cwd(cwd), env=process_environment(env),
            **(spawn_options() | kwargs),
        )
    return await _spawn(asyncio.create_subprocess_shell,
        command, cwd=process_cwd(cwd), env=process_environment(env),
        **(spawn_options() | kwargs),
    )


async def terminate_tree(proc: asyncio.subprocess.Process) -> None:
    from .process_types import OwnedProcessHandle
    if isinstance(proc, OwnedProcessHandle):
        await proc.terminate_owned_tree()
        return
    if sys.platform == "win32":
        if proc.returncode is None:
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                await asyncio.to_thread(
                    subprocess.run, ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW, timeout=3, check=False,
                )
    else:
        # Every managed process starts a session. Address its process group
        # even when the leader exited while a descendant still holds pipes.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(proc.wait(), 3)


@dataclass(frozen=True)
class ProcessOutput:
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool
    stderr_truncated: bool


async def capture_output(proc: asyncio.subprocess.Process, *, timeout: float,
                         limit: int = 5 * 1024 * 1024) -> tuple[bytes, bytes]:
    """For parsers: never interpret truncated diagnostic output as complete."""
    result = await communicate_bounded(proc, timeout=timeout, limit=limit)
    if result.stdout_truncated or result.stderr_truncated:
        raise RuntimeError(f"Process output exceeded {limit} bytes; results are incomplete.")
    return result.stdout, result.stderr


async def communicate_bounded(proc: asyncio.subprocess.Process, *, timeout: float,
                              limit: int = 5 * 1024 * 1024,
                              input: bytes | None = None) -> ProcessOutput:
    """Drain while retaining only bounded bytes; timeout includes pipe EOF.

    Cancellation and timeout both reap the process tree. Reader cleanup has a
    deadline so inherited handles cannot leave the task waiting indefinitely.
    """
    async def read(stream: asyncio.StreamReader | None) -> tuple[bytes, bool]:
        data = bytearray()
        truncated = False
        if stream is not None:
            while chunk := await stream.read(65536):
                remaining = max(0, limit - len(data))
                data.extend(chunk[:remaining])
                truncated |= len(chunk) > remaining
        return bytes(data), truncated

    async def write() -> None:
        if proc.stdin is not None:
            try:
                if input is not None:
                    proc.stdin.write(input)
                    await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                proc.stdin.close()

    tasks = [asyncio.create_task(read(proc.stdout)), asyncio.create_task(read(proc.stderr)),
             asyncio.create_task(write()), asyncio.create_task(proc.wait())]

    async def cleanup() -> None:
        try:
            # Even a successful command can leave detached workers which
            # closed their inherited stdout/stderr handles.
            await terminate_tree(proc)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    try:
        stdout, stderr, _, _ = await asyncio.wait_for(asyncio.gather(*tasks), timeout)
        return ProcessOutput(stdout[0], stderr[0], stdout[1], stderr[1])
    finally:
        # Retain the same cleanup on success, timeout and cancellation. Shield
        # prevents an interrupted owner from cancelling it; continuing to await
        # it prevents that owner from releasing resources while reaping runs.
        cleaning = asyncio.create_task(cleanup())
        cancelled = None
        while not cleaning.done():
            try:
                await asyncio.shield(cleaning)
            except asyncio.CancelledError as exc:
                cancelled = cancelled or exc
        cleaning.result()
        if cancelled is not None:
            raise cancelled
