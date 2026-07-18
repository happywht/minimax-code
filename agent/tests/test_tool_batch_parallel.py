"""Tests for the R23 two-phase parallel tool dispatcher.

Pins the five contracts the new batch dispatcher introduces when it
ports grok-build's ``xai-tool-runtime`` concurrency model into the
MiniMax agent core:

* :meth:`AgentCore._write_lock_for` — the pure serialization rule. Write
  tools (``write_file`` / ``edit_file``) keyed by their target file path
  share one :class:`asyncio.Lock` per path; reads, searches, terminals
  and path-less writes return ``None`` and stay fully concurrent.
* :meth:`AgentCore._run_tool_batch` — phase-1 ``prepare`` is serial
  (interactive consent, breaker entry and the ``tool_call`` /
  ``tool_running`` status emits must stay ordered); phase-2 ``execute``
  overlaps I/O via :func:`asyncio.gather`, yet results come back in
  *call* order so tool messages align 1:1 with the LLM's ``tool_calls``.
* Same-path writes serialize (no interleaving write corruption) while
  cross-path writes run concurrently — the lock is per-path, not global.
* A permission-denied sibling short-circuits in ``prepare`` without
  blocking the batch's other calls (no double-emit on the execute side).
* A batch-boundary cancellation fills the unprepared tail with failed
  ``cancelled`` results so every ``tool_call`` id still gets a tool
  result — the OpenAI tool-call ↔ tool-result pairing stays complete.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from minimax_code.agent.core import AgentConfig, AgentCore
from minimax_code.agent.tools import Tool, ToolRegistry, ToolResult

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeLLM:
    """LLM stand-in. ``stream_chat`` is never exercised here — we drive
    :meth:`AgentCore._run_tool_batch` directly — but :class:`AgentCore`
    needs *something* at construction time."""

    async def stream_chat(self, *args: Any, **kwargs: Any):  # pragma: no cover
        yield  # unreachable: kept the body a generator
        raise AssertionError("stream_chat must not be called by these tests")


class _SlowTool(Tool):
    """Sleeps a fixed delay so a test can tell parallel from serial apart.

    Two calls dispatched concurrently overlap (total ≈ max delay);
    dispatched serially they don't (total ≈ sum delay).
    """

    name = "slow"
    description = "sleeps then returns"
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "delay": {"type": "number"}},
        "required": ["delay"],
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        delay = float(kwargs.get("delay", 0.2))
        await asyncio.sleep(delay)
        return ToolResult.ok(output={"slept": delay, "path": kwargs.get("path")})


class _WriteTool(Tool):
    """A write-style tool (its name is in ``_WRITE_TOOLS``) so the per-file
    lock engages. Records an enter/exit bracket around its body so a test
    can assert same-path calls never overlap and cross-path calls do."""

    name = "write_file"
    description = "writes a file"
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path"],
    }

    def __init__(self) -> None:
        # (path, tag, monotonic) — appended under the per-file lock.
        self.brackets: list[tuple[str, str, float]] = []

    async def run(self, **kwargs: Any) -> ToolResult:
        path = str(kwargs.get("path"))
        # Hold the lock long enough that overlap (or its absence) is
        # observable in the bracket ordering, not just in timing.
        self.brackets.append((path, "enter", time.monotonic()))
        await asyncio.sleep(0.1)
        self.brackets.append((path, "exit", time.monotonic()))
        return ToolResult.ok(output={"wrote": path})


class _PingTool(Tool):
    """A second, distinct tool name so a batch can mix an allowed call with
    a denied one — isolation is only observable when the two siblings have
    *different* permission outcomes, and ``_check_rule`` keys on tool name."""

    name = "ping"
    description = "returns immediately"
    parameters = {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs: Any) -> ToolResult:
        return ToolResult.ok(output={"pong": True})


class _DenyStore:
    """Duck-typed PermissionStore — only ``lookup`` is read by ``_check_rule``.

    Denies the ``slow`` tool and allows everything else (e.g. ``ping``),
    so one batch sibling short-circuits while the rest execute.
    """

    def lookup(self, tool_name: str) -> dict[str, Any] | None:
        if tool_name == "slow":
            return {"action": "deny"}
        return None


def _call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    """Build one OpenAI-style tool_call payload (arguments JSON-encoded)."""
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def _make_core(
    *, registry: ToolRegistry, config: AgentConfig | None = None,
) -> AgentCore:
    return AgentCore(llm=_FakeLLM(), registry=registry, config=config or AgentConfig())


# ---------------------------------------------------------------------------
# _write_lock_for — pure serialization rule
# ---------------------------------------------------------------------------


def test_write_lock_none_for_read_and_nonwrite_tools() -> None:
    """Read / search / terminal tools never lock — they run fully concurrent."""
    core = _make_core(registry=ToolRegistry())
    assert core._write_lock_for("read_file", {"path": "/a"}) is None
    assert core._write_lock_for("search_files", {"query": "x"}) is None
    assert core._write_lock_for("exec_command", {"cmd": "ls"}) is None


def test_write_lock_keyed_by_path_across_write_tools() -> None:
    """Same path ⇒ one shared lock (write_file + edit_file agree); different
    path ⇒ a distinct lock. Mirrors grok's ``lock_path_for_args``."""
    core = _make_core(registry=ToolRegistry())
    lock_a1 = core._write_lock_for("write_file", {"path": "/a"})
    lock_a2 = core._write_lock_for("edit_file", {"file_path": "/a"})
    lock_b = core._write_lock_for("write_file", {"path": "/b"})
    assert lock_a1 is not None and lock_a2 is not None and lock_b is not None
    assert lock_a1 is lock_a2  # same path → same lock object
    assert lock_b is not lock_a1  # different path → different lock


def test_write_lock_none_when_path_missing() -> None:
    """A write call with no resolvable path is not serialized (nothing to key on)."""
    core = _make_core(registry=ToolRegistry())
    assert core._write_lock_for("write_file", {}) is None
    assert core._write_lock_for("write_file", {"path": ""}) is None


# ---------------------------------------------------------------------------
# _run_tool_batch — parallel timing + call-order preservation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_runs_independent_tools_in_parallel() -> None:
    """Two slow tools in one batch overlap: total wall time ≈ max(delay),
    not sum(delay). Serial dispatch would take ≥ 2× delay."""
    tool = _SlowTool()
    reg = ToolRegistry()
    reg.register(tool)
    core = _make_core(registry=reg)

    batch = [
        _call("slow", {"delay": 0.25}, "c1"),
        _call("slow", {"delay": 0.25}, "c2"),
    ]
    t0 = time.monotonic()
    results = await core._run_tool_batch(batch)
    elapsed = time.monotonic() - t0

    assert len(results) == 2
    assert all(r.success for r in results)
    # Two 0.25s sleeps: parallel ≤ ~0.30s (headroom for scheduling /
    # Windows timer granularity); serial ≥ 0.50s. 0.45s splits them.
    assert elapsed < 0.45, f"batch executed serially, took {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_batch_preserves_call_order_in_results() -> None:
    """``gather`` returns results in submission order; the batch must
    preserve that so tool messages align 1:1 with the LLM's tool_calls —
    even when an earlier call finishes *after* a later one."""
    tool = _SlowTool()
    reg = ToolRegistry()
    reg.register(tool)
    core = _make_core(registry=reg)

    # First call sleeps 6× longer than the second; in completion order
    # they'd swap. Submission order must win.
    batch = [
        _call("slow", {"delay": 0.30, "path": "first"}, "c_first"),
        _call("slow", {"delay": 0.05, "path": "second"}, "c_second"),
    ]
    results = await core._run_tool_batch(batch)
    assert [r.output["path"] for r in results] == ["first", "second"]


# ---------------------------------------------------------------------------
# Per-file lock semantics through the batch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_path_writes_serialize() -> None:
    """Two write_file calls to the SAME path must not overlap (per-file
    lock). Their enter/exit brackets must be strictly nested —
    ``enter, exit, enter, exit`` — never interleaved."""
    tool = _WriteTool()
    reg = ToolRegistry()
    reg.register(tool)
    core = _make_core(registry=reg)

    batch = [
        _call("write_file", {"path": "/shared"}, "w1"),
        _call("write_file", {"path": "/shared"}, "w2"),
    ]
    await core._run_tool_batch(batch)

    shared_tags = [tag for (path, tag, _t) in tool.brackets if path == "/shared"]
    assert shared_tags == ["enter", "exit", "enter", "exit"], (
        f"same-path writes interleaved (expected strict nesting): {shared_tags}"
    )


@pytest.mark.asyncio
async def test_cross_path_writes_run_concurrently() -> None:
    """Two write_file calls to DIFFERENT paths are not locked against each
    other — they must overlap, proving the lock is per-path, not global."""
    tool = _WriteTool()
    reg = ToolRegistry()
    reg.register(tool)
    core = _make_core(registry=reg)

    batch = [
        _call("write_file", {"path": "/a"}, "wa"),
        _call("write_file", {"path": "/b"}, "wb"),
    ]
    t0 = time.monotonic()
    await core._run_tool_batch(batch)
    elapsed = time.monotonic() - t0

    # A global lock would serialize two 0.1s writes to ≥ 0.20s; per-path
    # locking lets them overlap → ≈ 0.11s. 0.16s splits them with margin
    # for Windows timer granularity.
    assert elapsed < 0.16, f"cross-path writes serialized, took {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Short-circuit isolation + cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_denied_tool_short_circuits_without_blocking_siblings() -> None:
    """A permission-denied call short-circuits in ``prepare`` (emitted +
    audited there) and returns a failed result; siblings in the same batch
    still execute. The execute side does not double-emit the rejection."""
    reg = ToolRegistry()
    reg.register(_SlowTool())
    reg.register(_PingTool())
    core = _make_core(registry=reg)
    core._permission_store = _DenyStore()  # type: ignore[assignment]

    batch = [
        _call("ping", {}, "ok_call"),  # allowed — runs normally
        _call("slow", {"delay": 0.05}, "denied_call"),  # denied — short-circuits
    ]
    results = await core._run_tool_batch(batch)

    assert len(results) == 2
    assert results[0].success is True  # allowed sibling ran normally
    assert results[1].success is False  # denied short-circuit
    assert "deny" in (results[1].error or "").lower()


@pytest.mark.asyncio
async def test_batch_cancellation_fills_tail_with_cancelled_results() -> None:
    """A batch-boundary cancellation marks every unprepared slot as
    cancelled, but each ``tool_call`` id still receives a (failed)
    result — the OpenAI tool-call ↔ tool-result pairing stays complete
    even when nothing actually dispatches."""
    tool = _SlowTool()
    reg = ToolRegistry()
    reg.register(tool)
    core = _make_core(registry=reg)

    core.cancel()  # flip the flag before the batch prepares anything
    batch = [
        _call("slow", {"delay": 0.05}, "c1"),
        _call("slow", {"delay": 0.05}, "c2"),
        _call("slow", {"delay": 0.05}, "c3"),
    ]
    results = await core._run_tool_batch(batch)

    # Pairing intact: one result per call id, all cancelled, none dispatched.
    assert len(results) == 3
    assert all(r.success is False for r in results)
    assert all("cancel" in (r.error or "").lower() for r in results)
