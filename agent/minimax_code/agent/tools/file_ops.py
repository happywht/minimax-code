"""File-system read / write / list tools.

All three tools route through :func:`_safe_resolve` to enforce the
path-safety policy documented in ``docs/architecture.md`` §9:

* No ``..`` traversal that escapes the user-supplied root
  (``MINIMAX_CODE_WORKSPACE`` env or the current working directory
  if not set).
* No access to well-known sensitive directories
  (``~/.ssh``, ``~/.aws``, ``~/.config/gh``, Windows
  ``C:\\Windows\\System32``, macOS ``/private/etc``, …).
* Absolute paths must resolve to a location under the workspace.

A tool call that violates any of these returns a
:class:`~.ToolResult` with ``success=False`` and a short error
string the LLM can act on.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any

from ...workspace_ctx import current_root, current_run_id, env_or_cwd_root
from .base import Tool, ToolResult, register_tool
from .sandbox import overlay_read_target, redirect_write_target

# ---------------------------------------------------------------------------
# Path-safety policy
# ---------------------------------------------------------------------------

_SENSITIVE_DIR_NAMES = {
    ".ssh",
    ".aws",
    ".gnupg",
    ".config/gh",
    "Windows/System32",
    "Windows/SysWOW64",
    "Windows/Security",
    "Program Files",
    "Program Files (x86)",
    "ProgramData/Microsoft",
    "private/etc",
}


def _default_workspace() -> Path:
    """Resolve the workspace root the tools are allowed to touch.

    Session-scoped root first (per-project workspace, published on the
    ContextVar by ``agent.send_message``); otherwise falls back to
    ``MINIMAX_CODE_WORKSPACE`` or the current working directory. The
    path is resolved to an absolute, symlink-free form before being
    used in comparisons.
    """
    root = current_root()
    if root is not None:
        return root
    return env_or_cwd_root()


def _is_sensitive(p: Path) -> bool:
    """Return ``True`` if ``p`` lives under any of the sensitive dirs.

    Match is name-segment based — ``~/.ssh-old/notes`` is **not**
    blocked, but ``~/.ssh/keys`` is. This avoids both false
    positives (long working dirs that merely *contain* the
    substring ``ssh``) and false negatives (a directory named
    ``foo.ssh`` at the root).
    """
    try:
        parts = p.parts
    except (ValueError, OSError):
        return False
    lowered = [part.lower() for part in parts]
    for blocked in _SENSITIVE_DIR_NAMES:
        blocked_parts = [seg.lower() for seg in blocked.split("/")]
        if all(b in lowered for b in blocked_parts):
            # Need the relative position: the blocked prefix must
            # appear contiguously from the home dir forward.
            for i in range(len(lowered) - len(blocked_parts) + 1):
                if lowered[i : i + len(blocked_parts)] == blocked_parts:
                    return True
    return False


def safe_resolve(path: str | os.PathLike[str], workspace: Path | None = None) -> Path:
    """Resolve ``path`` against the workspace, applying the safety checks.

    Returns the canonical absolute :class:`Path`. Raises
    :class:`PathSecurityError` for any violation.
    """
    ws = (workspace or _default_workspace()).resolve()
    raw = Path(path).expanduser()

    # Reject empty / whitespace-only paths early.
    s = str(raw).strip()
    if not s:
        raise PathSecurityError("path must not be empty")

    # Reject obvious traversal tokens that survive path resolution
    # (defence-in-depth — the .resolve() below already strips
    # ``..``, but we also want a clear error message).
    if ".." in Path(s).parts:
        raise PathSecurityError(f"path traversal is not allowed: {path!r}")

    # Treat absolute paths as-is (after symlink resolution), or
    # anchor them under the workspace.
    if not raw.is_absolute():
        candidate = (ws / raw).resolve()
    else:
        candidate = raw.resolve()

    # Containment check via Path.is_relative_to (py3.9+).
    try:
        candidate.relative_to(ws)
    except ValueError as exc:
        raise PathSecurityError(
            f"path {str(candidate)!r} is outside workspace {str(ws)!r}"
        ) from exc

    if _is_sensitive(candidate):
        raise PathSecurityError(
            f"access to sensitive directory is blocked: {candidate!r}"
        )

    return candidate


class PathSecurityError(ValueError):
    """Raised when a tool argument violates the path-safety policy."""


def file_sha256(path: Path) -> str | None:
    """Streaming sha256 hex digest of ``path``'s on-disk bytes.

    v1.5.0 CAS foundation. Two invariants:

    * Always hash the bytes on disk, never an in-memory string —
      ``edit_file`` writes with default newline translation on
      Windows, so ``updated.encode()`` can differ from what landed.
      Hashing the file keeps read/write/edit on one footing
      (disk bytes).
    * Never raises — ``None`` means "hash unavailable" and callers
      treat it as CAS-not-applicable rather than an error.
    """
    digest = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# In-flight write registry (v1.5.1 — cross-run write awareness)
# ---------------------------------------------------------------------------

#: Process-wide map of ``normcase(path) -> run_id`` for files an in-flight
#: sub-agent run has declared an interest in. Purely advisory: writes are
#: never blocked on it — the registry only powers a ``concurrent_writer``
#: warning in the tool output so the LLM can coordinate (or guard the
#: write with ``expected_sha256``). Entries are released wholesale in
#: ``subagents._drive_run``'s finally; a hung run legitimately keeps its
#: claims (it may still write again), and process death clears everything.
_INFLIGHT_WRITES: dict[str, str] = {}


def _reg_key(path: Path | str) -> str:
    """Case-normalised registry key (Windows paths fold to one entry)."""
    return os.path.normcase(str(path))


def in_flight_writer(path: Path | str, *, exclude: str | None = None) -> str | None:
    """The run id currently claiming *path*, if any (excluding *exclude*)."""
    owner = _INFLIGHT_WRITES.get(_reg_key(path))
    if owner is not None and owner != exclude:
        return owner
    return None


def claim_write(path: Path | str, run_id: str) -> None:
    """Declare that *run_id* is writing *path* (first claim wins per run)."""
    key = _reg_key(path)
    if _INFLIGHT_WRITES.get(key) != run_id:
        _INFLIGHT_WRITES[key] = run_id


def release_run_writes(run_id: str) -> None:
    """Drop every claim held by *run_id* (called on run completion)."""
    for key in [k for k, owner in _INFLIGHT_WRITES.items() if owner == run_id]:
        del _INFLIGHT_WRITES[key]


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


@register_tool
class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a UTF-8 (or latin-1) text file. Returns the file contents "
        "as a string, optionally clipped to a 1-indexed inclusive line "
        "range. Binary files are rejected with an error."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path or path relative to the workspace."},
            "start_line": {"type": "integer", "minimum": 1, "description": "1-indexed first line to read."},
            "end_line": {"type": "integer", "minimum": 1, "description": "1-indexed last line to read (inclusive)."},
            "max_bytes": {
                "type": "integer",
                "minimum": 1024,
                "default": 1_048_576,
                "description": "Hard cap on returned text size in bytes (default 1 MiB).",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        path = kwargs.get("path")
        if not isinstance(path, str) or not path:
            return ToolResult.fail("'path' must be a non-empty string")
        try:
            target = safe_resolve(path)
        except PathSecurityError as exc:
            return ToolResult.fail(str(exc))

        # v1.5.0 sandbox: reads resolve through the overlay — the
        # sub-agent sees its own sandboxed writes plus the untouched
        # workspace originals (see sandbox.overlay_read_target).
        read_target = overlay_read_target(target)

        if not read_target.exists():
            return ToolResult.fail(f"file not found: {target}")
        if read_target.is_dir():
            return ToolResult.fail(f"path is a directory, not a file: {target}")

        max_bytes = int(kwargs.get("max_bytes") or 1_048_576)
        start_line = kwargs.get("start_line")
        end_line = kwargs.get("end_line")

        try:
            with read_target.open("rb") as fh:
                data = fh.read(max_bytes + 1)
        except OSError as exc:
            return ToolResult.fail(f"read failed: {exc}")
        truncated = len(data) > max_bytes
        if truncated:
            data = data[:max_bytes]

        # Cheap binary sniff — any NUL byte in the first 8 KiB
        # means this is not text we want to ship to the model.
        if b"\x00" in data[:8192]:
            return ToolResult.fail(
                "binary file detected (NUL byte) — refusing to read"
            )

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = data.decode("latin-1")
            except UnicodeDecodeError:
                return ToolResult.fail("file is not decodable as utf-8 or latin-1")

        # v1.5.0 CAS: fingerprint the raw bytes we read (pre-decode,
        # pre-line-slice) so it always describes the full file on disk.
        # Truncated reads cannot offer a whole-file hash — null means
        # "CAS unavailable for this read".
        sha = None if truncated else hashlib.sha256(data).hexdigest()

        total_lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        if start_line is not None or end_line is not None:
            lines = text.splitlines()
            s = max(1, int(start_line or 1))
            e = min(len(lines), int(end_line or len(lines)))
            if s > len(lines):
                return ToolResult.fail(
                    f"start_line {s} beyond file length ({len(lines)} lines)"
                )
            selected = lines[s - 1 : e]
            text = "\n".join(selected) + ("\n" if selected and e < len(lines) else "")

        output: dict[str, Any] = {
            "path": str(target),
            "content": text,
            "truncated": truncated,
            "sha256": sha,
        }
        if read_target != target:
            output["sandboxed"] = True
            output["sandbox_path"] = str(read_target)
        return ToolResult.ok(
            output=output,
            total_lines=total_lines,
            returned_bytes=len(text.encode("utf-8")),
        )


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


@register_tool
class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Overwrite a file with the given UTF-8 text content. Creates "
        "intermediate parent directories as needed. The whole file is "
        "replaced; for surgical edits prefer the 'edit_file' tool."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Target file path."},
            "content": {"type": "string", "description": "Full file body. May be empty."},
            "expected_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
                "description": (
                    "Optional optimistic lock: the sha256 the file had when you "
                    "last read it. On mismatch (or if the file no longer exists) "
                    "the write fails with the current hash — re-read the file, "
                    "then reapply your change."
                ),
            },
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        path = kwargs.get("path")
        content = kwargs.get("content")
        if not isinstance(path, str) or not path:
            return ToolResult.fail("'path' must be a non-empty string")
        if not isinstance(content, str):
            return ToolResult.fail("'content' must be a string")

        try:
            target = safe_resolve(path)
        except PathSecurityError as exc:
            return ToolResult.fail(str(exc))

        # v1.5.1 — cross-run write awareness. Register this run's claim
        # on the *workspace* path (the merge target for sandboxed runs)
        # and surface any rival in-flight run as an advisory warning.
        # Never blocks — the registry is coordination, not a lock; pair
        # with expected_sha256 when the warning fires.
        run_id = current_run_id()
        concurrent_writer = in_flight_writer(target, exclude=run_id)
        if run_id:
            claim_write(target, run_id)

        # v1.5.0 sandbox: CAS verifies the overlay read view (mirror if
        # present, workspace original otherwise); the write itself is
        # redirected into the sandbox mirror with a COW baseline
        # snapshot of the original on first touch. Without a sandbox
        # both collapse onto ``target`` — byte-for-byte the v1.5.0-CAS
        # behavior.
        read_target = overlay_read_target(target)
        write_target = redirect_write_target(target)

        if read_target.exists() and read_target.is_dir():
            return ToolResult.fail(f"path is an existing directory: {target}")

        existed = read_target.exists()

        # v1.5.0 CAS — optimistic lock. Hash the on-disk bytes *before*
        # the write; if the caller's last-read fingerprint no longer
        # matches, someone else touched the file and we refuse rather
        # than silently clobber (the v1.4.2 field report's finding #2).
        previous_sha = file_sha256(read_target) if existed else None
        expected = kwargs.get("expected_sha256")
        if expected is not None:
            if not isinstance(expected, str) or len(expected) != 64:
                return ToolResult.fail("'expected_sha256' must be a 64-char hex string")
            if not existed:
                return ToolResult.fail(
                    "write_file blocked: file no longer exists (deleted since "
                    "your read)",
                    output={
                        "path": str(target),
                        "expected_sha256": expected,
                        "file_exists": False,
                    },
                )
            if previous_sha != expected.strip().lower():
                return ToolResult.fail(
                    "write_file blocked: file changed since your read (sha256 "
                    "mismatch) — re-read the file and reapply your change",
                    output={
                        "path": str(target),
                        "expected_sha256": expected,
                        "current_sha256": previous_sha,
                        "file_exists": True,
                    },
                )

        # Pre-write backup for existing files (best-effort, never blocks).
        # Backs up the file being overwritten as the caller sees it —
        # the sandbox mirror when sandboxed, the original otherwise.
        backup_meta = None
        if existed:
            try:
                from ..backup import BackupManager
                backup_meta = BackupManager().backup(read_target)
            except Exception:
                pass

        try:
            write_target.parent.mkdir(parents=True, exist_ok=True)
            # ``newline=""`` keeps the bytes the caller passed
            # verbatim — without it, Python on Windows would
            # silently rewrite ``\n`` to ``\r\n``, surprising
            # callers (and us) on round-trip.
            with write_target.open("w", encoding="utf-8", newline="") as fh:
                fh.write(content)
        except OSError as exc:
            return ToolResult.fail(f"write failed: {exc}")

        size = write_target.stat().st_size
        new_sha = file_sha256(write_target)
        # R16 — mirror the successful write onto the causal file-change bus.
        # Fail-open: a bus failure must never break the tool that just wrote.
        try:
            from ...app import ensure_fs_bus

            bus = ensure_fs_bus()
            if bus is not None:
                bus.emit(
                    "created" if not existed else "modified",
                    [str(write_target)],
                    "write_file",
                    size_bytes=size,
                    # v1.5.1 — attribute the write to its owning run so
                    # the main agent's change notes can skip its own.
                    **({"run_id": run_id} if run_id else {}),
                )
        except Exception:  # noqa: BLE001 — file write must never break on the bus
            pass
        output: dict[str, Any] = {
            "path": str(target),
            "created": not existed,
            "overwritten": existed,
            "size_bytes": size,
            "previous_sha256": previous_sha,
            "sha256": new_sha,
            **({"backup": backup_meta} if backup_meta else {}),
        }
        if concurrent_writer:
            output["concurrent_writer"] = concurrent_writer
            output["warning"] = (
                f"⚠ in-flight run {concurrent_writer} has also claimed this "
                "file — coordinate with it, or guard your write with "
                "expected_sha256 after a fresh read"
            )
        if write_target != target:
            output["sandboxed"] = True
            output["sandbox_path"] = str(write_target)
        return ToolResult.ok(
            output=output,
            created=not existed,
            size_bytes=size,
        )


# ---------------------------------------------------------------------------
# append_file (v1.5.2)
# ---------------------------------------------------------------------------


@register_tool
class AppendFileTool(Tool):
    name = "append_file"
    description = (
        "Append UTF-8 text to the end of a file, creating it (and parent "
        "directories) when missing. Built for very large files: write the "
        "first chunk with write_file, then stream the rest as append_file "
        "calls so each call's content stays well under the output budget. "
        "The content lands verbatim at the current tail — include a "
        "leading newline yourself when the existing tail has no trailing "
        "one."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Target file path."},
            "content": {
                "type": "string",
                "description": "Text to append at the end of the file.",
            },
            "expected_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
                "description": (
                    "Optional optimistic lock: the sha256 the file had when "
                    "you last read it. On mismatch (or if the file no longer "
                    "exists) the append fails with the current hash — "
                    "re-read the file, then reapply your append."
                ),
            },
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        path = kwargs.get("path")
        content = kwargs.get("content")
        if not isinstance(path, str) or not path:
            return ToolResult.fail("'path' must be a non-empty string")
        if not isinstance(content, str):
            return ToolResult.fail("'content' must be a string")

        try:
            target = safe_resolve(path)
        except PathSecurityError as exc:
            return ToolResult.fail(str(exc))

        # v1.5.1 registry — same advisory contract as write_file: claim
        # the workspace path for this run, surface rival in-flight runs.
        run_id = current_run_id()
        concurrent_writer = in_flight_writer(target, exclude=run_id)
        if run_id:
            claim_write(target, run_id)

        read_target = overlay_read_target(target)
        write_target = redirect_write_target(target)

        if read_target.exists() and read_target.is_dir():
            return ToolResult.fail(f"path is an existing directory: {target}")

        existed = read_target.exists()

        # v1.5.2 — sandbox staging, the append-specific correctness step.
        # ``redirect_write_target`` snapshots the original into ``_base``
        # but does NOT seed the mirror itself; opening a fresh mirror in
        # "a" mode would start from empty, and the eventual collect's
        # three-way compare would then happily overwrite the workspace
        # with a file that lost its pre-append content. Seed the mirror
        # from the overlay read view (the workspace original on first
        # touch) so the mirror is always the full file. Fail-closed: this
        # copy is correctness, not visibility.
        if (
            write_target != read_target
            and read_target.exists()
            and read_target.is_file()
            and not write_target.exists()
        ):
            try:
                write_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(read_target, write_target)
            except OSError as exc:
                return ToolResult.fail(
                    f"append failed while staging the sandbox mirror: {exc}"
                )

        # v1.5.0 CAS — the lock guards the pre-append state of the file.
        previous_sha = file_sha256(read_target) if existed else None
        expected = kwargs.get("expected_sha256")
        if expected is not None:
            if not isinstance(expected, str) or len(expected) != 64:
                return ToolResult.fail("'expected_sha256' must be a 64-char hex string")
            if not existed:
                return ToolResult.fail(
                    "append_file blocked: file no longer exists (deleted since "
                    "your read)",
                    output={
                        "path": str(target),
                        "expected_sha256": expected,
                        "file_exists": False,
                    },
                )
            if previous_sha != expected.strip().lower():
                return ToolResult.fail(
                    "append_file blocked: file changed since your read (sha256 "
                    "mismatch) — re-read the file and reapply your append",
                    output={
                        "path": str(target),
                        "expected_sha256": expected,
                        "current_sha256": previous_sha,
                        "file_exists": True,
                    },
                )

        # Pre-append backup (best-effort) — the tail being extended as
        # the caller sees it: sandbox mirror when sandboxed, original
        # otherwise.
        backup_meta = None
        if existed:
            try:
                from ..backup import BackupManager
                backup_meta = BackupManager().backup(read_target)
            except Exception:
                pass

        appended_bytes = len(content.encode("utf-8"))
        try:
            write_target.parent.mkdir(parents=True, exist_ok=True)
            # "a" + newline="": byte-faithful tail writes, same contract
            # as write_file's "w" — Windows never rewrites our \n.
            with write_target.open("a", encoding="utf-8", newline="") as fh:
                fh.write(content)
        except OSError as exc:
            return ToolResult.fail(f"append failed: {exc}")

        size = write_target.stat().st_size
        new_sha = file_sha256(write_target)
        # R16 — mirror the append onto the causal file-change bus (fail-open).
        try:
            from ...app import ensure_fs_bus

            bus = ensure_fs_bus()
            if bus is not None:
                bus.emit(
                    "created" if not existed else "modified",
                    [str(write_target)],
                    "append_file",
                    size_bytes=size,
                    **({"run_id": run_id} if run_id else {}),
                )
        except Exception:  # noqa: BLE001 — the append must never break on the bus
            pass
        output: dict[str, Any] = {
            "path": str(target),
            "created": not existed,
            "appended_bytes": appended_bytes,
            "size_bytes": size,
            "previous_sha256": previous_sha,
            "sha256": new_sha,
            **({"backup": backup_meta} if backup_meta else {}),
        }
        if concurrent_writer:
            output["concurrent_writer"] = concurrent_writer
            output["warning"] = (
                f"⚠ in-flight run {concurrent_writer} has also claimed this "
                "file — coordinate with it, or guard your append with "
                "expected_sha256 after a fresh read"
            )
        if write_target != target:
            output["sandboxed"] = True
            output["sandbox_path"] = str(write_target)
        return ToolResult.ok(output=output, created=not existed, size_bytes=size)


# ---------------------------------------------------------------------------
# list_directory
# ---------------------------------------------------------------------------


@register_tool
class ListDirectoryTool(Tool):
    name = "list_directory"
    description = (
        "List immediate children of a directory. Each entry includes the "
        "name, whether it is a directory, and the file size in bytes "
        "(0 for directories). The optional glob-style 'pattern' argument "
        "filters by name (e.g. '*.py')."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory to list. Defaults to workspace root."},
            "pattern": {"type": "string", "description": "Optional fnmatch glob applied to file names."},
        },
        "required": [],
        "additionalProperties": False,
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        path = kwargs.get("path") or kwargs.get("path", ".")
        if not isinstance(path, str):
            return ToolResult.fail("'path' must be a string")
        try:
            target = safe_resolve(path)
        except PathSecurityError as exc:
            return ToolResult.fail(str(exc))

        if not target.exists():
            return ToolResult.fail(f"directory not found: {target}")
        if not target.is_dir():
            return ToolResult.fail(f"not a directory: {target}")

        pattern = kwargs.get("pattern")
        try:
            entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError as exc:
            return ToolResult.fail(f"list failed: {exc}")

        out: list[dict[str, Any]] = []
        for entry in entries:
            if pattern and not _fnmatch(entry.name, pattern):
                continue
            try:
                is_dir = entry.is_dir()
                size = 0 if is_dir else entry.stat().st_size
            except OSError:
                is_dir, size = False, 0
            out.append(
                {
                    "name": entry.name,
                    "path": str(entry),
                    "is_dir": is_dir,
                    "size_bytes": size,
                }
            )
        return ToolResult.ok(output={"directory": str(target), "entries": out},
                             count=len(out))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fnmatch(name: str, pattern: str) -> bool:
    """Small fnmatch wrapper — kept module-local for testability."""
    import fnmatch as _fn

    return _fn.fnmatchcase(name, pattern)


__all__ = [
    "ReadFileTool",
    "WriteFileTool",
    "ListDirectoryTool",
    "PathSecurityError",
    "safe_resolve",
]
