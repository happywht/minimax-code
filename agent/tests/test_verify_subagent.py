"""P0-5 ``verify_subagent`` + P1-3 ``build_subagent_status`` projector tests.

verify_subagent runs a real headless command (subprocess) pinned to the
workspace root — pass / fail / timeout / cwd containment are all live.
The prefix-bypass pin matters most: the original ``startswith`` check
let ``C:\\ws-evil`` sneak past a ``C:\\ws`` root; ``is_relative_to``
must hold. The projector tests pin the superset contract that folded
the legacy ``_snapshot`` into one unified projection: the *full*
``tool_calls`` list, the *conditional* ``files_written`` key, and the
``waiting_deps`` / ``cancelled`` / error states.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from minimax_code.agent.tools.base import ToolResult, get_default_registry
from minimax_code.agent.tools.verification import build_subagent_status
from minimax_code.workspace_ctx import reset_current_root, set_current_root


@pytest.fixture
def workspace_root(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    token = set_current_root(root)
    try:
        yield root
    finally:
        reset_current_root(token)


async def _dispatch(**kwargs) -> ToolResult:
    return await get_default_registry().dispatch("verify_subagent", kwargs)


def _py(command: str) -> str:
    # Quote the interpreter — tmp paths can contain spaces.
    return f'"{sys.executable}" {command}'


# ---------------------------------------------------------------------------
# verify_subagent — live subprocess runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_pass(workspace_root: Path) -> None:
    res = await _dispatch(command=_py('-c "print(42)"'))
    assert res.success, res.error
    assert res.output["passed"] is True
    assert res.output["exit_code"] == 0
    # No timed_out key on the success path — only the timeout branch
    # carries it.
    assert "timed_out" not in res.output
    assert "42" in res.output["stdout"]


@pytest.mark.asyncio
async def test_verify_fail_surfaces_stderr(workspace_root: Path) -> None:
    bad = workspace_root / "broken.py"
    bad.write_text("def oops(:\n", encoding="utf-8")
    res = await _dispatch(command=_py(f"-m py_compile {bad.name}"))
    assert res.success, res.error  # the *tool* ran; the check failed
    assert res.output["passed"] is False
    assert res.output["exit_code"] != 0
    assert res.output["stderr"].strip()


@pytest.mark.asyncio
async def test_verify_timeout_kills_process(workspace_root: Path) -> None:
    res = await _dispatch(
        command=_py('-c "import time; time.sleep(5)"'), timeout_s=1
    )
    assert not res.success  # timed_out is a failure-shaped result
    out = res.output
    assert out["timed_out"] is True
    assert out["passed"] is False
    # Process-tree kill (taskkill /F /T on Windows, killpg on POSIX):
    # without it the orphaned child held the stdout pipe and wait()
    # blocked for the sleeper's full 5s. ~1s kill point is the pin.
    assert out["elapsed_s"] < 4


@pytest.mark.asyncio
async def test_verify_cwd_subdir_allowed(workspace_root: Path) -> None:
    sub = workspace_root / "sub"
    sub.mkdir()
    res = await _dispatch(command=_py('-c "print(1)"'), cwd="sub")
    assert res.success, res.error
    assert res.output["passed"] is True


@pytest.mark.asyncio
async def test_verify_cwd_prefix_bypass_rejected(tmp_path: Path) -> None:
    """Pin: ``ws-evil`` must NOT pass a ``ws`` root via string prefix."""
    root = tmp_path / "proj"
    root.mkdir()
    evil = tmp_path / "proj-evil"
    evil.mkdir()
    token = set_current_root(root)
    try:
        res = await _dispatch(command=_py('-c "print(1)"'), cwd="../proj-evil")
        assert not res.success
        assert "escapes workspace root" in (res.error or "")
    finally:
        reset_current_root(token)


@pytest.mark.asyncio
async def test_verify_no_root_rejected() -> None:
    res = await _dispatch(command=_py('-c "print(1)"'))
    assert not res.success
    assert "no workspace root" in (res.error or "")


@pytest.mark.asyncio
async def test_verify_empty_command_rejected(workspace_root: Path) -> None:
    res = await _dispatch(command="   ")
    assert not res.success


# ---------------------------------------------------------------------------
# build_subagent_status — unified projector contract (pure function)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_projector_finished_superset_contract(workspace_root: Path) -> None:
    task_result = {
        "text": "done",
        "iterations": 3,
        "tool_calls": [{"name": "a"}, {"name": "b"}, {"name": "c"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2},
        "cancelled": False,
        "files_written": ["src/x.py", "src/y.py"],
        "reported": True,
        "stub": False,
    }
    snap = build_subagent_status("run_fin", task_result=task_result)
    assert snap["run_id"] == "run_fin"
    assert snap["status"] == "completed"
    # Full list, not a tail/summary — the legacy _snapshot contract.
    assert snap["tool_calls"] == [{"name": "a"}, {"name": "b"}, {"name": "c"}]
    assert snap["files_written"] == ["src/x.py", "src/y.py"]
    assert snap["iterations"] == 3
    assert snap["text"] == "done"
    assert snap["reported"] is True
    assert snap["stub"] is False
    assert "error" not in snap


@pytest.mark.asyncio
async def test_projector_files_written_conditional(workspace_root: Path) -> None:
    snap = build_subagent_status(
        "run_no_files", task_result={"text": "", "tool_calls": []}
    )
    # Key omitted when the envelope never carried it — not null, not [].
    assert "files_written" not in snap


@pytest.mark.asyncio
async def test_projector_failed_run_keeps_completed_status(workspace_root: Path) -> None:
    snap = build_subagent_status(
        "run_err", task_result={"error": "boom", "cancelled": False}
    )
    # Documented convention: failure is completed + error key, so wait
    # and check envelopes compare apples-to-apples.
    assert snap["status"] == "completed"
    assert snap["error"] == "boom"


@pytest.mark.asyncio
async def test_projector_cancelled_run(workspace_root: Path) -> None:
    snap = build_subagent_status(
        "run_cx", task_result={"cancelled": True, "text": "partial"}
    )
    assert snap["status"] == "cancelled"
    assert snap["cancelled"] is True


@pytest.mark.asyncio
async def test_projector_waiting_deps(workspace_root: Path) -> None:
    snap = build_subagent_status(
        "run_wait", started_at=time.time() - 2.0, deps_pending=["run_a", "run_b"]
    )
    assert snap["status"] == "waiting_deps"
    assert snap["deps_pending"] == ["run_a", "run_b"]
    assert "elapsed_s" in snap
    assert snap["elapsed_s"] >= 1.9
    # Terminal keys never leak into an in-flight projection.
    assert "text" not in snap and "tool_calls" not in snap


@pytest.mark.asyncio
async def test_projector_running_defaults(workspace_root: Path) -> None:
    snap = build_subagent_status("run_go", started_at=time.time())
    assert snap["status"] == "running"
    assert snap["deps_pending"] == []
