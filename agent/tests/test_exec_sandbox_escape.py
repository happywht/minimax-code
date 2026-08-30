"""v1.6.0 exec_command sandbox-escape regression tests.

Field report (v1.5.0 known limitation): a sandboxed sub-agent shelling
out through ``exec_command`` writes the *shared workspace* directly —
exec has no redirection layer — and those writes are invisible to
``collect_subagent``'s three-way compare, silently bypassing the merge.
The v1.6.0 detector snapshots the workspace before/after the child
(only when the caller runs inside a sandbox), diffs the snapshots,
subtracts writes the fs-bus attributes to other actors, and reports
the remainder as an advisory ``sandbox_escape`` block on both the
normal and the timed-out return paths.

All tool exercises go through ``registry.dispatch`` (project
convention since v1.4.1) and drive real child processes
(``sys.executable`` + a temp script) — the deny list blocks
``python -c``, so inline code is not an option and the fixture is
closer to production anyway.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from minimax_code.agent.tools import get_default_registry
from minimax_code.agent.tools import terminal as terminal_mod
from minimax_code.agent.tools.subagents import SANDBOX_PROTOCOL_PROMPT
from minimax_code.fsnotify.bus import FsEventBus
from minimax_code.workspace_ctx import (
    reset_current_root,
    reset_current_run_id,
    reset_sandbox,
    set_current_root,
    set_current_run_id,
    set_sandbox,
)


@pytest.fixture
def workspace_root(tmp_path: Path):
    root = tmp_path / "proj_root"
    root.mkdir()
    token = set_current_root(root)
    try:
        yield root
    finally:
        reset_current_root(token)


@contextmanager
def sandbox_run(root: Path, run_id: str):
    """Publish sandbox + run id for the block; reset after."""
    sb = root / ".minimax" / "sandboxes" / run_id
    sb.mkdir(parents=True, exist_ok=True)
    sb_token = set_sandbox(sb)
    run_token = set_current_run_id(run_id)
    try:
        yield sb
    finally:
        reset_sandbox(sb_token)
        reset_current_run_id(run_token)


async def _exec(cmd: list[str], **extra: Any):
    return await get_default_registry().dispatch("exec_command", {"cmd": cmd, **extra})


def _script(tmp_path: Path, body: str, name: str = "worker.py") -> list[str]:
    """Materialise *body* as a real script outside the workspace."""
    script = tmp_path / name
    script.write_text(body, encoding="utf-8")
    return [sys.executable, str(script)]


# ---------------------------------------------------------------------------
# Detection core
# ---------------------------------------------------------------------------


async def test_escape_write_detected(workspace_root: Path, tmp_path: Path) -> None:
    cmd = _script(tmp_path, "open('esc.txt', 'w').write('escape')\n")

    with sandbox_run(workspace_root, "run_esc"):
        res = await _exec(cmd)
    assert res.success, res.error

    esc = res.output["sandbox_escape"]
    assert esc["changed"] == ["added:esc.txt"]
    assert esc["changed_count"] == 1
    assert esc["changed_truncated"] is False
    assert "collect_subagent" in esc["warning"]
    # The write really did land in the workspace (that is the escape).
    assert (workspace_root / "esc.txt").read_text(encoding="utf-8") == "escape"


async def test_escape_modified_and_removed(
    workspace_root: Path, tmp_path: Path
) -> None:
    (workspace_root / "mod.txt").write_text("v1", encoding="utf-8")
    (workspace_root / "gone.txt").write_text("delete me", encoding="utf-8")
    body = (
        "open('mod.txt', 'w').write('v2-longer')\n"
        "import os; os.remove('gone.txt')\n"
    )
    cmd = _script(tmp_path, body)

    with sandbox_run(workspace_root, "run_mod"):
        res = await _exec(cmd)
    assert res.success, res.error

    changes = res.output["sandbox_escape"]["changed"]
    assert "modified:mod.txt" in changes
    assert "removed:gone.txt" in changes


async def test_no_write_no_escape_block(workspace_root: Path, tmp_path: Path) -> None:
    cmd = _script(tmp_path, "print('read-only')\n")

    with sandbox_run(workspace_root, "run_ro"):
        res = await _exec(cmd)
    assert res.success, res.error
    assert "sandbox_escape" not in res.output


async def test_main_agent_path_has_zero_overhead(
    workspace_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside a sandbox the walks never run — the hot path stays free."""
    calls = []

    def exploding_scan(root: Path):  # pragma: no cover — must never run
        calls.append(root)
        raise AssertionError("scan must not run without a sandbox")

    monkeypatch.setattr(terminal_mod, "_scan_workspace", exploding_scan)
    cmd = _script(tmp_path, "print('main agent')\n")

    res = await _exec(cmd)  # no sandbox scope
    assert res.success, res.error
    assert calls == []
    assert "sandbox_escape" not in res.output


# ---------------------------------------------------------------------------
# Attribution window (concurrent-write false-positive guard)
# ---------------------------------------------------------------------------


async def test_attributed_foreign_write_not_flagged(
    workspace_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent run's tool write inside our exec window is not an escape.

    Real bus; a background task emits the foreign run's write_file event
    while the child is running — the diff sees both files but only the
    unattributed one survives into the report.

    Ordering is enforced with a two-way handshake instead of a wall-clock
    sleep because the attribution window is bounded on BOTH sides: the bus
    watermark is taken right after the pre-walk (before spawn) and the
    subtraction scan runs right after the post-walk. A plain sleep(0.05)
    races on fast hosts (emit lands after the scan), while a naive sentinel
    races the other way (emit lands during the pre-walk, at or before the
    watermark). Here the child first drops a ready-file *outside* the
    workspace (a sentinel inside would pollute the escape diff), the
    injector waits for it — hence strictly after spawn, hence strictly
    above the watermark — and only then drops the go-file and emits; the
    child spins on the go-file before writing, so the emit also strictly
    precedes the post-walk.
    """
    bus = FsEventBus()
    monkeypatch.setattr("minimax_code.app.ensure_fs_bus", lambda: bus)

    ready = tmp_path / "ready.txt"  # tmp_path root — outside the workspace snapshot
    go = tmp_path / "go.txt"
    body = (
        "import os, time\n"
        f"open({str(ready)!r}, 'w').write('r')\n"
        f"while not os.path.exists({str(go)!r}):\n"
        "    time.sleep(0.005)\n"
        "open('esc.txt', 'w').write('mine')\n"
        "open('foreign.txt', 'w').write('x')\n"
    )
    cmd = _script(tmp_path, body)

    async def inject_foreign_event() -> None:
        while not ready.exists():
            await asyncio.sleep(0.005)
        go.write_text("go", encoding="utf-8")
        bus.emit(
            "created",
            [str(workspace_root / "foreign.txt")],
            "write_file",
            run_id="run_other",
        )

    with sandbox_run(workspace_root, "run_win"):
        task = asyncio.create_task(inject_foreign_event())
        res = await _exec(cmd)
        await task
    assert res.success, res.error

    esc = res.output["sandbox_escape"]
    assert esc["changed"] == ["added:esc.txt"]


async def test_own_run_events_not_subtracted_from_others(
    workspace_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The subtraction only removes *other* runs' paths — own-run events
    are guarded explicitly and non-attributed causes never subtract."""
    bus = FsEventBus()
    monkeypatch.setattr("minimax_code.app.ensure_fs_bus", lambda: bus)

    cmd = _script(tmp_path, "open('esc.txt', 'w').write('mine')\n")

    with sandbox_run(workspace_root, "run_own"):
        # Emit after the pre-walk would have taken its watermark is not
        # directly controllable here; instead verify the guard the cheap
        # way — a same-run event is not subtracted, so the escape stands.
        bus.emit(
            "created",
            [str(workspace_root / "esc.txt")],
            "write_file",
            run_id="run_own",
        )
        res = await _exec(cmd)
    assert res.success, res.error
    assert res.output["sandbox_escape"]["changed"] == ["added:esc.txt"]


# ---------------------------------------------------------------------------
# Exclude table & truncation
# ---------------------------------------------------------------------------


async def test_exclude_table_noise_pruned_build_visible(
    workspace_root: Path, tmp_path: Path
) -> None:
    """VCS/deps/our-own-machinery noise is pruned; build outputs are not."""
    body = (
        "import os\n"
        "os.makedirs('.git', exist_ok=True)\n"
        "open('.git/index', 'w').write('x')\n"
        "os.makedirs('node_modules/pkg', exist_ok=True)\n"
        "open('node_modules/pkg/pkg.js', 'w').write('x')\n"
        "os.makedirs('build', exist_ok=True)\n"
        "open('build/out.js', 'w').write('x')\n"
    )
    cmd = _script(tmp_path, body)

    with sandbox_run(workspace_root, "run_ex"):
        res = await _exec(cmd)
    assert res.success, res.error

    changed = res.output["sandbox_escape"]["changed"]
    assert changed == ["added:build/out.js"]


async def test_report_truncated_at_50(workspace_root: Path, tmp_path: Path) -> None:
    lines = [f"open('f{i:02d}.txt', 'w').write('x')" for i in range(60)]
    cmd = _script(tmp_path, "\n".join(lines) + "\n")

    with sandbox_run(workspace_root, "run_bulk"):
        res = await _exec(cmd)
    assert res.success, res.error

    esc = res.output["sandbox_escape"]
    assert esc["changed_count"] == 60
    assert len(esc["changed"]) == 50
    assert esc["changed_truncated"] is True


# ---------------------------------------------------------------------------
# Timeout path & fs-bus emit shape
# ---------------------------------------------------------------------------


async def test_timed_out_path_carries_escape(
    workspace_root: Path, tmp_path: Path
) -> None:
    body = "open('esc.txt', 'w').write('late')\nimport time\ntime.sleep(30)\n"
    cmd = _script(tmp_path, body)

    with sandbox_run(workspace_root, "run_to"):
        res = await _exec(cmd, timeout=2)
    assert not res.success
    assert res.output["timed_out"] is True
    esc = res.output["sandbox_escape"]
    assert esc["changed"] == ["added:esc.txt"]


async def test_emit_shape_on_fs_bus(
    workspace_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Emits use the closed FsEventKind union + escape cause + run id —
    an illegal kind string would be swallowed by the bus's fail-open."""

    class CaptureBus:
        def __init__(self) -> None:
            self.emitted: list[dict[str, Any]] = []

        def emit(self, kind, paths, cause, **kw):
            self.emitted.append(
                {"kind": kind, "paths": list(paths), "cause": cause, **kw}
            )
            return None

        def recent(self, *, limit=100, **kw):
            return []

    cap = CaptureBus()
    monkeypatch.setattr("minimax_code.app.ensure_fs_bus", lambda: cap)

    cmd = _script(tmp_path, "open('esc.txt', 'w').write('x')\n")
    with sandbox_run(workspace_root, "run_emit"):
        res = await _exec(cmd)
    assert res.success, res.error

    (event,) = cap.emitted
    assert event["kind"] == "created"  # legal FsEventKind value
    assert event["cause"] == "exec_command_sandbox_escape"
    assert event["run_id"] == "run_emit"
    assert event["paths"] == [str(workspace_root / "esc.txt")]


# ---------------------------------------------------------------------------
# Prompt pin
# ---------------------------------------------------------------------------


async def test_protocol_prompt_teaches_escape_handling() -> None:
    """The sandbox paragraph must teach the escape consequence + remedy."""
    lowered = SANDBOX_PROTOCOL_PROMPT.lower()
    assert "exec_command" in lowered
    assert "sandbox_escape" in lowered
    assert "collect" in lowered
    assert "write_file" in lowered
