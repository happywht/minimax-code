"""v1.5.0 — per-run sub-agent write sandbox (layer 2).

Concurrent field report: parallel sub-agents sharing one workspace root
produced last-write-wins clobbers. These tests pin the sandbox contract:

* Write redirect — a sandboxed ``write_file`` / ``edit_file`` lands in
  ``<root>/.minimax/sandboxes/<run_id>/`` while the workspace original
  stays untouched; ``_base/`` keeps a COW snapshot as the merge baseline.
* Overlay reads — ``read_file`` and ``edit_file``'s internal read both
  resolve through the sandbox mirror, so a second edit cannot silently
  resurrect the workspace original and lose the first sandboxed edit.
* Opt-in only — without ``set_sandbox`` the tools write the workspace
  directly (byte-for-byte the pre-v1.5.0 behavior).
* Fail-closed spawn — ``spawn_subagent(sandbox=true)`` refuses to run
  when the sandbox directory cannot be created; a silent fallback to
  the shared workspace would void the contract the caller opted into.

Tool exercises go through ``registry.dispatch`` (project convention
since v1.4.1); the spawn-side integration tests reuse the scripted
SubAgentRuntime fixtures from the async-lifecycle track.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from pathlib import Path
from typing import Any

import pytest

from minimax_code.agent.tools import get_default_registry
from minimax_code.agent.tools.sandbox import BASE_DIR, SANDBOX_RELPATH
from minimax_code.agent.tools.subagents import (
    REPORT_PROTOCOL_PROMPT,
    SANDBOX_PROTOCOL_PROMPT,
    SpawnSubagentTool,
)
from minimax_code.orchestrator.subagent import (
    SubAgentRuntime,
    get_background_run,
    set_subagent_runtime,
)
from minimax_code.storage.dao.agents import AgentDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path
from minimax_code.workspace_ctx import (
    current_sandbox,
    reset_current_root,
    reset_sandbox,
    set_current_root,
    set_sandbox,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def app_db(tmp_path: Path):
    from minimax_code import app
    from minimax_code.storage.dao.sessions import SessionsDAO

    db = AsyncDatabase(make_temp_database_path(tmp_path))
    await db.connect()
    await db.migrate()
    monkey = pytest.MonkeyPatch()
    monkey.setattr(app, "_DB_SINGLETON", db)
    app.set_sessions_dao(SessionsDAO(db))
    try:
        yield db
    finally:
        monkey.undo()
        app.set_sessions_dao(None)
        await db.close()


@pytest.fixture
def stub_runtime():
    prev = set_subagent_runtime(SubAgentRuntime())
    try:
        yield SubAgentRuntime()
    finally:
        set_subagent_runtime(prev)


@pytest.fixture
def workspace_root(tmp_path: Path):
    """Point the v1.3.0 workspace ContextVar at a temp project root."""
    root = tmp_path / "proj_root"
    root.mkdir()
    token = set_current_root(root)
    try:
        yield root
    finally:
        reset_current_root(token)


@pytest.fixture
def sandbox_scope(workspace_root: Path):
    """Publish a run sandbox via the ContextVar; always reset the token.

    Mirrors what ``subagents._drive_run`` does for a sandboxed spawn:
    the tests below drive tools through ``registry.dispatch`` while this
    scope is active, exercising the exact code path a real sandboxed
    sub-agent hits.
    """
    sb = workspace_root / SANDBOX_RELPATH / "run_testscope"
    sb.mkdir(parents=True)
    token = set_sandbox(sb)
    try:
        yield sb
    finally:
        reset_sandbox(token)


async def _dispatch(name: str, args: dict) -> Any:
    return await get_default_registry().dispatch(name, args)


async def _seed_agent(db: AsyncDatabase) -> str:
    dao = AgentDAO(db)
    name = f"sbx_{uuid.uuid4().hex[:8]}"
    await dao.upsert(name=name, system_prompt="you are a test agent")
    return name


# ---------------------------------------------------------------------------
# Unit layer — write redirect / COW / overlay reads (set_sandbox + dispatch)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_redirected_into_sandbox(
    workspace_root: Path, sandbox_scope: Path
) -> None:
    res = await _dispatch(
        "write_file", {"path": "src/generated.py", "content": "# sandboxed"}
    )
    assert res.success, res.error
    # The workspace never sees the file; the sandbox mirror holds it.
    assert not (workspace_root / "src").exists()
    mirror = sandbox_scope / "src" / "generated.py"
    assert mirror.read_text(encoding="utf-8") == "# sandboxed"
    # No COW baseline for a brand-new file.
    assert not (sandbox_scope / BASE_DIR).exists() or not any(
        (sandbox_scope / BASE_DIR).rglob("*")
    )


@pytest.mark.asyncio
async def test_default_off_writes_workspace_directly(workspace_root: Path) -> None:
    """Regression pin: without set_sandbox the tools touch the workspace."""
    res = await _dispatch("write_file", {"path": "direct.txt", "content": "v1"})
    assert res.success, res.error
    assert (workspace_root / "direct.txt").read_text(encoding="utf-8") == "v1"
    assert "sandboxed" not in res.output
    assert not (workspace_root / ".minimax" / "sandboxes").exists()


@pytest.mark.asyncio
async def test_cow_baseline_snapshots_workspace_original(
    workspace_root: Path, sandbox_scope: Path
) -> None:
    target = workspace_root / "a.py"
    target.write_text("original\n", encoding="utf-8")

    res = await _dispatch(
        "edit_file",
        {"path": "a.py", "old_string": "original", "new_string": "modified"},
    )
    assert res.success, res.error

    # Workspace original is untouched; the baseline captured it; the
    # mirror holds the edit. This triple is collect_subagent's input.
    assert target.read_text(encoding="utf-8") == "original\n"
    baseline = sandbox_scope / BASE_DIR / "a.py"
    assert baseline.read_text(encoding="utf-8") == "original\n"
    mirror = sandbox_scope / "a.py"
    assert mirror.read_text(encoding="utf-8") == "modified\n"


@pytest.mark.asyncio
async def test_overlay_read_reflects_own_writes(
    workspace_root: Path, sandbox_scope: Path
) -> None:
    (workspace_root / "f.txt").write_text("v1", encoding="utf-8")

    w = await _dispatch("write_file", {"path": "f.txt", "content": "v2"})
    assert w.success, w.error
    r = await _dispatch("read_file", {"path": "f.txt"})
    assert r.success, r.error
    # The sandboxed agent reads its own write back, with the flag saying so.
    assert r.output["content"] == "v2"
    assert r.output["sandboxed"] is True
    assert r.output["sandbox_path"] == str(sandbox_scope / "f.txt")
    # The workspace original is still v1 — only the view is overlaid.
    assert (workspace_root / "f.txt").read_text(encoding="utf-8") == "v1"


@pytest.mark.asyncio
async def test_second_edit_keeps_first_edit(
    workspace_root: Path, sandbox_scope: Path
) -> None:
    """Design ruling (ii): edit_file's internal read goes through the
    overlay too — otherwise the second edit would silently resurrect the
    workspace original and lose the first sandboxed edit."""
    (workspace_root / "e.txt").write_text("alpha\nbeta\n", encoding="utf-8")

    first = await _dispatch(
        "edit_file", {"path": "e.txt", "old_string": "alpha", "new_string": "gamma"}
    )
    assert first.success, first.error
    second = await _dispatch(
        "edit_file", {"path": "e.txt", "old_string": "beta", "new_string": "delta"}
    )
    assert second.success, second.error

    mirror = sandbox_scope / "e.txt"
    assert mirror.read_text(encoding="utf-8") == "gamma\ndelta\n"
    assert (workspace_root / "e.txt").read_text(encoding="utf-8") == "alpha\nbeta\n"


@pytest.mark.asyncio
async def test_write_output_reports_sandbox_path(
    workspace_root: Path, sandbox_scope: Path
) -> None:
    res = await _dispatch("write_file", {"path": "notes.md", "content": "hi"})
    assert res.success, res.error
    # ``path`` stays the workspace address the LLM asked for; the extra
    # keys disclose where the bytes actually landed.
    assert res.output["path"] == str(workspace_root / "notes.md")
    assert res.output["sandboxed"] is True
    assert res.output["sandbox_path"] == str(sandbox_scope / "notes.md")


@pytest.mark.asyncio
async def test_dot_minimax_paths_pass_through(
    workspace_root: Path, sandbox_scope: Path
) -> None:
    """Artifacts / backups / sandbox internals must never be re-redirected
    (nested-sandbox and ``_base`` self-collision hazards)."""
    res = await _dispatch(
        "write_file", {"path": ".minimax/notes/x.md", "content": "meta"}
    )
    assert res.success, res.error
    assert (workspace_root / ".minimax" / "notes" / "x.md").read_text(
        encoding="utf-8"
    ) == "meta"
    assert "sandboxed" not in res.output
    assert not (sandbox_scope / "notes").exists()


# ---------------------------------------------------------------------------
# Spawn integration — prompt, metadata, ContextVar chain, fail-closed
# ---------------------------------------------------------------------------


def test_prompt_appends_sandbox_protocol_last() -> None:
    """Source-order pin: the sandbox paragraph is concatenated after the
    report protocol (and after the task-precedence declaration)."""
    src = inspect.getsource(SpawnSubagentTool.run)
    # Anchor on the concatenation expressions themselves — the constant
    # names also appear in the leading comments, which would skew a
    # naive first-occurrence comparison.
    assert src.index("+ TASK_PRECEDENCE_PROMPT") < src.index(
        "+ REPORT_PROTOCOL_PROMPT"
    )
    assert src.index("+ REPORT_PROTOCOL_PROMPT") < src.index(
        "+ (SANDBOX_PROTOCOL_PROMPT if sandbox else \"\")"
    )
    # And the constants themselves are distinct, non-empty teachings.
    assert SANDBOX_PROTOCOL_PROMPT.strip()
    assert SANDBOX_PROTOCOL_PROMPT is not REPORT_PROTOCOL_PROMPT


@pytest.mark.asyncio
async def test_spawn_sandbox_true_creates_dir_and_envelope(
    app_db: AsyncDatabase, stub_runtime, workspace_root: Path
) -> None:
    name = await _seed_agent(app_db)
    out = await SpawnSubagentTool().run(agent_name=name, prompt="sb work", sandbox=True)
    assert out.success, out.error
    run_id = out.output["run_id"]
    assert (workspace_root / SANDBOX_RELPATH / run_id).is_dir()
    assert out.output["sandbox"] is True


@pytest.mark.asyncio
async def test_spawn_default_reports_no_sandbox(
    app_db: AsyncDatabase, stub_runtime, workspace_root: Path
) -> None:
    name = await _seed_agent(app_db)
    out = await SpawnSubagentTool().run(agent_name=name, prompt="plain work")
    assert out.success, out.error
    assert out.output["sandbox"] is False
    assert not (workspace_root / SANDBOX_RELPATH).exists()
    # Inline (wait=True) runs reset the ContextVar in the finally block.
    assert current_sandbox() is None


@pytest.mark.asyncio
async def test_sandbox_context_cleared_after_inline_run(
    app_db: AsyncDatabase, stub_runtime, workspace_root: Path
) -> None:
    name = await _seed_agent(app_db)
    out = await SpawnSubagentTool().run(
        agent_name=name, prompt="inline sb", sandbox=True
    )
    assert out.success, out.error
    # The token reset in _drive_run's finally must hold even though the
    # drive ran on this very task (wait=True shares the caller context).
    assert current_sandbox() is None


@pytest.mark.asyncio
async def test_background_run_contextvar_chain(
    app_db: AsyncDatabase, workspace_root: Path
) -> None:
    """wait=False spawns the drive on a copied context — prove the sandbox
    ContextVar set inside ``_drive_run`` reaches tools dispatched from the
    runtime by writing a file mid-run and checking where it landed."""

    class WritingRuntime(SubAgentRuntime):
        async def invoke(self, handle, *, session_id: str, request: str) -> dict:
            res = await get_default_registry().dispatch(
                "write_file",
                {"path": "src/generated.py", "content": "# from sub-agent"},
            )
            assert res.success, res.error
            return {
                "agent": handle.config.name,
                "request": request,
                "session_id": session_id,
                "text": "written",
                "cancelled": False,
                "iterations": 1,
                "tool_calls": [{"name": "write_file"}],
                "stub": False,
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                "truncated": False,
            }

    prev = set_subagent_runtime(WritingRuntime())
    try:
        name = await _seed_agent(app_db)
        out = await SpawnSubagentTool().run(
            agent_name=name, prompt="bg write", wait=False, sandbox=True
        )
        assert out.success, out.error
        assert out.output["sandbox"] is True
        run_id = out.output["run_id"]

        task = get_background_run(run_id)
        assert task is not None
        await asyncio.wait_for(asyncio.shield(task), timeout=10)
        await asyncio.sleep(0)  # let the done-callback run its pop
    finally:
        set_subagent_runtime(prev)

    # The write went into the run's sandbox, not the shared workspace —
    # the whole point of the feature.
    assert not (workspace_root / "src").exists()
    sb_dir = workspace_root / SANDBOX_RELPATH / run_id
    assert (sb_dir / "src" / "generated.py").read_text(encoding="utf-8") == (
        "# from sub-agent"
    )
    # And the spawning task's context was never polluted.
    assert current_sandbox() is None


@pytest.mark.asyncio
async def test_spawn_fails_closed_without_root(
    app_db: AsyncDatabase, stub_runtime, workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "minimax_code.agent.tools.sandbox.sandbox_root_for", lambda run_id: None
    )
    name = await _seed_agent(app_db)
    out = await SpawnSubagentTool().run(agent_name=name, prompt="x", sandbox=True)
    assert not out.success
    assert "no workspace root" in out.error


@pytest.mark.asyncio
async def test_spawn_fails_closed_on_mkdir_failure(
    app_db: AsyncDatabase, stub_runtime, workspace_root: Path,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A file squatting on the sandbox path makes mkdir(parents=True,
    # exist_ok=True) raise FileExistsError — stable cross-platform.
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"x")
    monkeypatch.setattr(
        "minimax_code.agent.tools.sandbox.sandbox_root_for",
        lambda run_id: blocker,
    )
    name = await _seed_agent(app_db)
    out = await SpawnSubagentTool().run(agent_name=name, prompt="x", sandbox=True)
    assert not out.success
    assert "directory creation failed" in out.error
