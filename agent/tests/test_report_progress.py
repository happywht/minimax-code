"""v1.5.2 report_progress regression tests — interim sub-agent progress.

Field report (round 4): a background sub-agent was invisible until it
finished — the coordinator could poll nothing but ``status=running``.
The protocol has three legs, all pinned here:

* the per-run ``report_progress`` tool appends JSON lines to the run's
  ``PROGRESS.jsonl`` ledger (and never leaks into the main registry);
* ``progress_summary`` projects the ledger into the check / wait /
  finished-run responses;
* the milestone paragraph reaches the spawned system prompt and the
  cloned allowlist.

Tool exercises dispatch through a registry — a fresh clone with the
per-run tool registered, mirroring how the sub-agent's runtime actually
calls it.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from minimax_code.agent.tools.artifacts import (
    PROGRESS_NAME,
    progress_summary,
    read_progress,
)
from minimax_code.agent.tools.base import ToolRegistry, get_default_registry
from minimax_code.agent.tools.subagents import (
    PROGRESS_PROTOCOL_PROMPT,
    _clone_registry_with_report,
)
from minimax_code.agent.tools.subagents import (
    _emit_safe as _real_emit_safe,
)
from minimax_code.agent.tools.verification import build_subagent_status
from minimax_code.workspace_ctx import reset_current_root, set_current_root


@pytest.fixture
def workspace_root(tmp_path: Path):
    root = tmp_path / "proj_root"
    root.mkdir()
    token = set_current_root(root)
    try:
        yield root
    finally:
        reset_current_root(token)


def _run_registry(run_id: str) -> ToolRegistry:
    """Clone + the per-run tools, exactly like a real spawn builds it."""
    return _clone_registry_with_report(run_id, "tester", agent_id="agent-1")


async def _report(registry: ToolRegistry, args: dict) -> Any:
    return await registry.dispatch("report_progress", args)


# ---------------------------------------------------------------------------
# Ledger writes (through dispatch)
# ---------------------------------------------------------------------------


async def test_report_progress_appends_jsonl_entries(workspace_root: Path) -> None:
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    reg = _run_registry(run_id)

    first = await _report(reg, {"note": "phase 1 done"})
    assert first.success, first.error
    assert first.output["skipped"] is False
    assert first.output["entries_total"] == 1

    second = await _report(reg, {"note": "halfway", "percent": 50})
    assert second.success, second.error
    assert second.output["entries_total"] == 2

    ledger = workspace_root / ".minimax" / "artifacts" / run_id / PROGRESS_NAME
    lines = [
        json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()
    ]
    assert lines[0]["note"] == "phase 1 done"
    assert "percent" not in lines[0]
    assert lines[1]["percent"] == 50
    assert lines[1]["run_id"] == run_id


async def test_report_progress_validation(workspace_root: Path) -> None:
    reg = _run_registry("run_v")

    empty = await _report(reg, {"note": "   "})
    assert not empty.success

    bad_pct = await _report(reg, {"note": "x", "percent": "lots"})
    assert not bad_pct.success

    clamped = await _report(reg, {"note": "x", "percent": 150})
    assert clamped.success, clamped.error
    assert clamped.output["percent"] == 100


async def test_report_progress_no_root_skips() -> None:
    # No workspace root published: soft protocol, success with skipped.
    reg = _run_registry("run_nr")
    res = await _report(reg, {"note": "nowhere to write"})
    assert res.success, res.error
    assert res.output["skipped"] is True


async def test_report_progress_live_event_push(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "run_evt"
    reg = _run_registry(run_id)

    import minimax_code.agent.tools.subagents as subagents_mod
    from minimax_code.orchestrator.subagent import (
        pop_subagent_emit,
        register_subagent_emit,
        reset_parent_session,
        set_parent_session,
    )

    events: list[dict[str, Any]] = []

    async def fake(emit: Any, event: dict[str, Any]) -> None:
        events.append(event)

    monkeypatch.setattr(subagents_mod, "_emit_safe", fake)
    register_subagent_emit("sess_1", lambda event, payload: None)
    token = set_parent_session("sess_1")
    try:
        res = await _report(reg, {"note": "milestone reached", "percent": 40})
        assert res.success, res.error
    finally:
        reset_parent_session(token)
        pop_subagent_emit("sess_1")
        monkeypatch.setattr(subagents_mod, "_emit_safe", _real_emit_safe)

    (event,) = events
    assert event["run_id"] == run_id
    assert event["agent_id"] == "agent-1"
    assert event["parent_session_id"] == "sess_1"
    # Reuses the closed frontend status union; summary carries the note.
    assert event["status"] == "thinking"
    assert event["summary"] == "milestone reached"
    assert event["progress"] == pytest.approx(0.4)


async def test_report_progress_not_in_main_registry() -> None:
    names = [t.name for t in get_default_registry().list()]
    assert "report_progress" not in names
    assert "report_completion" not in names


# ---------------------------------------------------------------------------
# Ledger reads & summary projection
# ---------------------------------------------------------------------------


async def test_read_progress_skips_corrupt_lines(workspace_root: Path) -> None:
    run_id = "run_corrupt"
    base = workspace_root / ".minimax" / "artifacts" / run_id
    base.mkdir(parents=True)
    (base / PROGRESS_NAME).write_text(
        json.dumps({"note": "good 1"}) + "\n"
        + "{not json}\n"
        + "\n"
        + json.dumps({"note": "good 2", "percent": 10}) + "\n",
        encoding="utf-8",
    )
    entries = read_progress(run_id, limit=None)
    assert [e["note"] for e in entries] == ["good 1", "good 2"]


async def test_progress_summary_shape_and_empty(workspace_root: Path) -> None:
    run_id = "run_sum"
    reg = _run_registry(run_id)
    assert progress_summary(run_id) is None

    for note, pct in (("a", 10), ("b", 60), ("c", None), ("d", 90)):
        res = await _report(
            reg, {"note": note, **({"percent": pct} if pct is not None else {})}
        )
        assert res.success, res.error

    summary = progress_summary(run_id)
    assert summary is not None
    assert summary["total"] == 4
    assert [r["note"] for r in summary["recent"]] == ["b", "c", "d"]
    assert "percent" not in summary["recent"][1]  # entry c has no percent
    assert summary["latest_percent"] == 90


async def test_snapshot_includes_progress_when_present(workspace_root: Path) -> None:
    run_id = "run_snap"
    reg = _run_registry(run_id)
    await _report(reg, {"note": "halfway", "percent": 50})

    # v1.6.1 — the legacy _snapshot was folded into the unified
    # build_subagent_status projector (check/wait/finish all use it).
    snap = build_subagent_status(
        run_id, task_result={"text": "done", "tool_calls": [], "usage": {}}
    )
    assert snap["progress"]["total"] == 1
    assert snap["progress"]["latest_percent"] == 50

    # Absent when the sub-agent never reported — key omitted, not null.
    bare = build_subagent_status(
        "run_never_reported", task_result={"text": "", "tool_calls": []}
    )
    assert "progress" not in bare


# ---------------------------------------------------------------------------
# Prompt & allowlist wiring
# ---------------------------------------------------------------------------


async def test_progress_protocol_prompt_ordering() -> None:
    """Source-level pin: precedence → completion → progress → sandbox."""
    import inspect

    from minimax_code.agent.tools import subagents as mod

    src = inspect.getsource(mod.SpawnSubagentTool.run)
    # Anchor on the concatenation expressions, not bare names — the run
    # method's comments mention SANDBOX_PROTOCOL_PROMPT before the join.
    order = [
        src.index("+ TASK_PRECEDENCE_PROMPT"),
        src.index("+ REPORT_PROTOCOL_PROMPT"),
        src.index("+ PROGRESS_PROTOCOL_PROMPT"),
        src.index("+ (SANDBOX_PROTOCOL_PROMPT"),
    ]
    assert order == sorted(order)
    # The paragraph itself teaches milestones, not per-step noise.
    assert "milestone" in PROGRESS_PROTOCOL_PROMPT.lower()
    assert "report_completion" in PROGRESS_PROTOCOL_PROMPT


async def test_clone_registry_contains_both_report_tools() -> None:
    reg = _run_registry("run_clone")
    names = [t.name for t in reg.list()]
    assert "report_progress" in names
    assert "report_completion" in names


async def test_allowlist_source_appends_report_progress() -> None:
    """The non-None allowlist must carry both protocol tools."""
    import inspect

    from minimax_code.agent.tools import subagents as mod

    src = inspect.getsource(mod.SpawnSubagentTool.run)
    assert '"report_completion", "report_progress"' in src
