"""Shared memory / scratchpad for multi-agent coordination (P0-2).

A flat per-workspace KV store that sub-agents can read and write through
three tools (``shared_memory_put``, ``shared_memory_get``, ``shared_memory_list``).
The store is *file-backed* (one JSON file per workspace, with an advisory
file lock) so it survives agent restarts, is visible to every tool that
can read the workspace, and adds zero schema-migration risk.

Why file-backed (not a new DB table)
------------------------------------

Migrations are great when the shape is stable; this shape is exploratory
(ad-hoc namespaces, value sizes that span a few bytes to a few hundred
KB, read-after-write from sibling sub-agents). The file lives under
``.minimax/shared_memory/`` alongside the other on-disk state, is
single-writer safe via :func:`fcntl.flock` (Unix) / an OS-level lockfile
fallback on Windows, and the read path is one ``json.loads`` away.

Wire shape
----------

``put(namespace, key, value, scope="run"|"workspace")`` writes a value.
``get(namespace, key, scope=...)`` reads it (or a ``default``).
``list(scope=..., namespace=None)`` returns the visible entries.

Scope
-----

``scope="workspace"`` keys are visible to every agent running against
this workspace — the main agent, sub-agents, and any future orchestrator.
``scope="run"`` keys are namespaced to the current sub-agent's
``run_id`` (resolved via the ``workspace_ctx.current_run_id`` ContextVar
the write tools already publish) and invisible to other runs. Use
``scope="run"`` for scratch state and ``"workspace"`` for cross-agent
handoff signals.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from .base import Tool, ToolResult, register_tool

logger = logging.getLogger(__name__)

# Workspace-relative directory for the KV store. Lives alongside
# ``.minimax/artifacts/`` and ``.minimax/sandboxes/`` so everything
# minimax-owned sits under one tree.
_STORE_DIR_NAME = "shared_memory"
# Run-scope subdirectory (per-run keys never collide with workspace keys).
_RUN_PREFIX = "run_"
# Filename of the JSON store. One file keeps writes atomic via
# ``write_text`` + ``os.replace``; a multi-file scheme would need a
# write-ahead log to be safe across crashes.
_STORE_FILE_NAME = "store.json"
# Lockfile name (advisory; ``flock`` on Unix, OS lock on Windows).
_LOCK_FILE_NAME = ".lock"


def _store_root() -> Path | None:
    """Resolve ``<root>/.minimax/shared_memory`` for the active workspace.

    Returns ``None`` when no workspace root is active — callers must
    treat that as a hard skip (no store to read or write), never a
    failed tool call.
    """
    from ...workspace_ctx import current_root

    root = current_root()
    if root is None:
        return None
    return (root / ".minimax" / _STORE_DIR_NAME).resolve()


@contextlib.contextmanager
def _file_lock(path: Path, *, timeout_s: float = 2.0):
    """Best-effort cross-process lock around the store file.

    Unix uses ``fcntl.flock``; Windows uses an msvcrt ``lk`` file lock
    (best-effort, advisory). The timeout is short because a stuck lock
    is almost always a dead agent, not contention — fail-open and log
    so a hung tool can never hang the whole run.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = None
    locked = False
    try:
        fh = path.open("a+", encoding="utf-8")
        deadline = time.time() + timeout_s
        if os.name == "nt":
            try:
                import msvcrt  # type: ignore[import-not-found]

                while time.time() < deadline:
                    try:
                        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                        locked = True
                        break
                    except OSError:
                        time.sleep(0.02)
            except Exception:  # pragma: no cover — defensive
                locked = True  # fall back to no lock rather than hang
        else:
            try:
                import fcntl  # type: ignore[import-not-found]

                while time.time() < deadline:
                    try:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        locked = True
                        break
                    except OSError:
                        time.sleep(0.02)
            except Exception:  # pragma: no cover — defensive
                locked = True
        yield
    finally:
        if fh is not None:
            try:
                if locked and os.name != "nt":
                    import fcntl  # type: ignore[import-not-found]

                    try:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                    except Exception:
                        pass
                if locked and os.name == "nt":
                    try:
                        import msvcrt  # type: ignore[import-not-found]

                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    except Exception:
                        pass
            finally:
                fh.close()


def _read_store(root: Path) -> dict[str, Any]:
    """Load the store JSON; empty dict on first run or corrupt file.

    A corrupt file is quarantined (renamed ``store.json.corrupt-<ts>``
    so the incident is post-mortable) and treated as empty rather than
    raised — a malformed kv blob must never break a sub-agent run.
    The quarantine is best-effort: if the rename fails we still reset,
    leaving the original file for manual inspection.
    """
    target = root / _STORE_FILE_NAME
    if not target.is_file():
        return {}
    try:
        return json.loads(target.read_text(encoding="utf-8") or "{}")
    except (ValueError, OSError):
        logger.warning("shared_memory store %s unreadable; quarantining", target)
        try:
            target.replace(target.with_name(f"{_STORE_FILE_NAME}.corrupt-{int(time.time())}"))
        except OSError:
            logger.warning("shared_memory quarantine failed for %s", target)
        return {}


def _atomic_write(target: Path, data: str) -> None:
    """Write ``data`` atomically via a sibling tmp + ``os.replace``.

    ``Path.write_text`` truncates-and-writes, so a concurrent reader can
    see a half-written file. ``os.replace`` is atomic on POSIX and
    effectively atomic on Windows for files on the same volume.
    """
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, target)


def _write_store(root: Path, store: dict[str, Any]) -> None:
    target = root / _STORE_FILE_NAME
    _atomic_write(target, json.dumps(store, ensure_ascii=False, indent=2))


def _scope_key(scope: str, namespace: str, key: str, run_id: str | None) -> str:
    """Build the flat dict key for a (scope, namespace, key) triple.

    Run-scope keys carry the run_id prefix so a fresh sub-agent never
    inherits a previous run's scratchpad — and so two parallel sub-agents
    cannot trample each other even when both picked the same namespace.
    """
    if scope == "workspace":
        return f"workspace::{namespace}::{key}"
    if scope == "run":
        rid = run_id or "_anon"
        return f"{_RUN_PREFIX}{rid}::{namespace}::{key}"
    raise ValueError(f"scope must be 'workspace' or 'run', got {scope!r}")


def _current_run_id() -> str | None:
    """Best-effort current run id (works from inside a sub-agent drive).

    Reads the ContextVar the write tools publish; returns ``None`` when
    the caller is the main agent (in which case run-scope is meaningless
    and the tool surfaces a clear error).
    """
    try:
        from ...workspace_ctx import current_run_id

        return current_run_id()
    except Exception:  # pragma: no cover — defensive
        return None


@register_tool
class SharedMemoryPutTool(Tool):
    name = "shared_memory_put"
    description = (
        "Write a key/value into the workspace's shared memory (P0-2). "
        "Use namespace='<topic>' (e.g. 'contract', 'plan', 'handoff') "
        "and a stable key. scope='workspace' is visible to every agent "
        "running against this workspace (main + sub-agents); scope='run' "
        "is namespaced to this sub-agent and hidden from siblings. "
        "Subsequent shared_memory_get returns the same value."
    )
    parameters = {
        "type": "object",
        "properties": {
            "namespace": {
                "type": "string",
                "description": "Logical grouping, e.g. 'contract', 'plan', 'handoff'.",
            },
            "key": {
                "type": "string",
                "description": "Stable key within the namespace (e.g. 'render_engine').",
            },
            "value": {
                "description": (
                    "JSON-serialisable value (string, number, list, dict, bool, null). "
                    "For longer text, prefer a string."
                ),
            },
            "scope": {
                "type": "string",
                "enum": ["workspace", "run"],
                "description": (
                    "'workspace' (default) = visible to all agents. "
                    "'run' = private to this sub-agent's run_id."
                ),
                "default": "workspace",
            },
        },
        "required": ["namespace", "key", "value"],
        "additionalProperties": False,
    }

    async def run(
        self,
        namespace: str,
        key: str,
        value: Any,
        scope: str = "workspace",
    ) -> ToolResult:
        if not isinstance(namespace, str) or not namespace.strip():
            return ToolResult.fail("namespace must be a non-empty string")
        if not isinstance(key, str) or not key.strip():
            return ToolResult.fail("key must be a non-empty string")
        if scope not in ("workspace", "run"):
            return ToolResult.fail("scope must be 'workspace' or 'run'")
        root = _store_root()
        if root is None:
            return ToolResult.fail(
                "no workspace root is active — shared memory is unavailable"
            )
        run_id = _current_run_id() if scope == "run" else None
        if scope == "run" and not run_id:
            return ToolResult.fail(
                "scope='run' requires a sub-agent run_id; "
                "the main agent cannot use run-scope (use 'workspace')"
            )
        # json.dumps is the cheapest sanity check for non-serialisable values
        # (sets, custom objects) — surface a clean error instead of letting
        # the atomic-write step blow up later.
        try:
            json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            return ToolResult.fail(f"value is not JSON-serialisable: {exc}")
        try:
            with _file_lock(root / _LOCK_FILE_NAME):
                store = _read_store(root)
                flat = _scope_key(scope, namespace.strip(), key.strip(), run_id)
                entry = {
                    "value": value,
                    "namespace": namespace.strip(),
                    "key": key.strip(),
                    "scope": scope,
                    "run_id": run_id,
                    "updated_at": time.time(),
                }
                store[flat] = entry
                _write_store(root, store)
        except OSError as exc:
            return ToolResult.fail(f"shared memory write failed: {exc}")
        return ToolResult.ok(
            {
                "namespace": namespace.strip(),
                "key": key.strip(),
                "scope": scope,
                "run_id": run_id,
                "stored": True,
            }
        )


@register_tool
class SharedMemoryGetTool(Tool):
    name = "shared_memory_get"
    description = (
        "Read a value previously stored with shared_memory_put. "
        "Returns the value plus metadata (scope, run_id, updated_at). "
        "When the key is absent, returns {found:false} unless you pass "
        "a 'default' value."
    )
    parameters = {
        "type": "object",
        "properties": {
            "namespace": {
                "type": "string",
                "description": "Namespace used at write time.",
            },
            "key": {
                "type": "string",
                "description": "Key used at write time.",
            },
            "scope": {
                "type": "string",
                "enum": ["workspace", "run"],
                "description": "Same scope as the put.",
                "default": "workspace",
            },
            "default": {
                "description": "Value to return when the key is absent.",
            },
        },
        "required": ["namespace", "key"],
        "additionalProperties": False,
    }

    async def run(
        self,
        namespace: str,
        key: str,
        scope: str = "workspace",
        default: Any = None,
    ) -> ToolResult:
        if not isinstance(namespace, str) or not namespace.strip():
            return ToolResult.fail("namespace must be a non-empty string")
        if not isinstance(key, str) or not key.strip():
            return ToolResult.fail("key must be a non-empty string")
        if scope not in ("workspace", "run"):
            return ToolResult.fail("scope must be 'workspace' or 'run'")
        root = _store_root()
        if root is None:
            return ToolResult.ok(
                {"found": False, "reason": "no workspace root", "value": default}
            )
        run_id = _current_run_id() if scope == "run" else None
        try:
            store = _read_store(root)
        except OSError as exc:
            return ToolResult.fail(f"shared memory read failed: {exc}")
        flat = _scope_key(scope, namespace.strip(), key.strip(), run_id)
        entry = store.get(flat)
        if entry is None:
            return ToolResult.ok(
                {"found": False, "namespace": namespace.strip(), "key": key.strip(), "value": default}
            )
        return ToolResult.ok(
            {
                "found": True,
                "namespace": entry.get("namespace", namespace.strip()),
                "key": entry.get("key", key.strip()),
                "scope": entry.get("scope", scope),
                "run_id": entry.get("run_id"),
                "value": entry.get("value"),
                "updated_at": entry.get("updated_at"),
            }
        )


@register_tool
class SharedMemoryListTool(Tool):
    name = "shared_memory_list"
    description = (
        "List entries in shared memory, optionally filtered by namespace "
        "and/or scope. Use this to discover what earlier sub-agents "
        "wrote before you start a handoff chain."
    )
    parameters = {
        "type": "object",
        "properties": {
            "namespace": {
                "type": "string",
                "description": "If provided, only entries with this namespace.",
            },
            "scope": {
                "type": "string",
                "enum": ["workspace", "run"],
                "description": "If provided, only entries of this scope.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
                "description": "Maximum number of entries to return (default 100).",
            },
        },
        "additionalProperties": False,
    }

    async def run(
        self,
        namespace: str | None = None,
        scope: str | None = None,
        limit: int = 100,
    ) -> ToolResult:
        root = _store_root()
        if root is None:
            return ToolResult.ok({"entries": [], "count": 0, "reason": "no workspace root"})
        try:
            store = _read_store(root)
        except OSError as exc:
            return ToolResult.fail(f"shared memory read failed: {exc}")
        run_id = _current_run_id()
        ns_filter = namespace.strip() if isinstance(namespace, str) and namespace.strip() else None
        try:
            cap = max(1, min(int(limit), 500))
        except (TypeError, ValueError):
            cap = 100
        entries: list[dict[str, Any]] = []
        for _flat, entry in store.items():
            if scope is not None and entry.get("scope") != scope:
                continue
            if ns_filter is not None and entry.get("namespace") != ns_filter:
                continue
            # run-scope filter: hide other runs' scratch when caller asked
            # for "run" without supplying their own run id.
            if scope == "run" and entry.get("run_id") != run_id:
                continue
            entries.append(
                {
                    "namespace": entry.get("namespace"),
                    "key": entry.get("key"),
                    "scope": entry.get("scope"),
                    "run_id": entry.get("run_id"),
                    "updated_at": entry.get("updated_at"),
                }
            )
            if len(entries) >= cap:
                break
        return ToolResult.ok({"entries": entries, "count": len(entries)})


__all__ = [
    "SharedMemoryGetTool",
    "SharedMemoryListTool",
    "SharedMemoryPutTool",
]
