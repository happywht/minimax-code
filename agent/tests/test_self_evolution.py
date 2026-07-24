"""Tests for the self-evolution trajectory collector (R309, Layer 2).

Coverage map:

* :func:`run_once` fault-tolerance (git missing) + git history parsing.
* :meth:`SelfEvolutionReport.to_markdown` section rendering.
* :meth:`SelfEvolutionReport.write_to` same-day idempotency.
* :func:`build_payload_runner` dispatcher contract — noop echo when
  unarmed, collection when armed, structured error on failure, and the
  zero-regression guarantee (noop path ≡ scheduler ``_noop_runner``).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

from minimax_code.agent.self_evolution import build_payload_runner
from minimax_code.agent.self_evolution import payload as payload_mod
from minimax_code.agent.self_evolution import runner as runner_mod
from minimax_code.agent.self_evolution.runner import (
    CommitEntry,
    GitSnapshot,
    SelfEvolutionReport,
    ToolResult,
    run_once,
)
from minimax_code.scheduler import _noop_runner

# Fixed clock so date-stamped assertions do not depend on ``datetime.now``.
_CLOCK = datetime(2026, 7, 18, 9, 0, 0)
_DATE = "2026-07-18"


# ---------------------------------------------------------------------------
# run_once
# ---------------------------------------------------------------------------


async def test_run_once_tolerates_git_failure(monkeypatch, tmp_path):
    """A non-git cwd still yields a report — git error lands in ``errors``."""
    captured: dict[str, object] = {}

    async def fake_run_cmd(cmd, *, cwd, timeout_s):
        captured["cwd"] = cwd
        if cmd[0] == "git":
            return None, "", "", "not a git repository"
        # ruff / pytest simply succeed.
        return 0, "", "", None

    monkeypatch.setattr(runner_mod, "_run_cmd", fake_run_cmd)
    report = await run_once(str(tmp_path), now=_CLOCK)

    assert report.date == _DATE
    assert report.cwd == str(tmp_path)
    assert captured["cwd"] == str(tmp_path)
    assert report.git.error is not None
    assert report.git.commits == []
    assert any("git" in e for e in report.errors)


async def test_run_once_parses_git_history_and_tree_state(monkeypatch, tmp_path):
    """Branch + commit log + porcelain status are decoded into the snapshot."""
    log = "deadbeef\x1fAlice\x1f2026-07-17\x1ffix the thing\x1e"
    status = " M modified.py\n?? untracked.py\nA  staged.py\n"

    async def fake_run_cmd(cmd, *, cwd, timeout_s):
        if cmd[0] != "git":
            return 0, "", "", None
        sub = " ".join(cmd[1:])
        if "rev-parse" in sub:
            return 0, "main\n", "", None
        if "log" in sub:
            return 0, log, "", None
        if "status" in sub:
            return 0, status, "", None
        if "diff" in sub:
            return 0, " 1 file changed\n", "", None
        return 0, "", "", None

    monkeypatch.setattr(runner_mod, "_run_cmd", fake_run_cmd)
    report = await run_once(str(tmp_path), now=_CLOCK)

    assert report.git.error is None
    assert report.git.branch == "main"
    assert report.git.commits == [
        CommitEntry(sha="deadbeef", author="Alice", date="2026-07-17", message="fix the thing")
    ]
    assert report.git.modified == ["modified.py"]
    assert report.git.untracked == ["untracked.py"]
    assert report.git.staged == ["staged.py"]
    assert "1 file changed" in report.git.diff_stat


# ---------------------------------------------------------------------------
# SelfEvolutionReport rendering / persistence
# ---------------------------------------------------------------------------


def _sample_report(cwd: str = "/tmp/x") -> SelfEvolutionReport:
    return SelfEvolutionReport(
        date=_DATE,
        generated_at="2026-07-18T09:00:00",
        cwd=cwd,
        git=GitSnapshot(
            branch="main",
            commits=[
                CommitEntry(
                    sha="deadbeef", author="Alice", date="2026-07-17", message="fix the thing"
                )
            ],
            modified=["a.py"],
            untracked=[],
            staged=[],
            diff_stat=" 1 file changed",
            error=None,
        ),
        ruff=ToolResult(name="ruff", ran=True, returncode=0, stdout="All checks passed!", stderr="", error=None),
        pytest=ToolResult(name="pytest", ran=True, returncode=0, stdout="120 tests collected", stderr="", error=None),
        errors=[],
    )


def test_to_markdown_renders_all_sections():
    md = _sample_report().to_markdown()
    assert md.startswith(f"# Self-Evolution Report — {_DATE}")
    assert "## Git" in md
    assert "## Ruff" in md
    assert "## Pytest" in md
    assert "`main`" in md  # branch
    assert "deadbeef" in md  # commit sha prefix
    assert "All checks passed!" in md  # ruff stdout
    assert "120 tests collected" in md  # pytest stdout


def test_write_to_overwrites_same_day_report(tmp_path):
    """Idempotency: a second same-day write replaces the first, same path."""
    report_dir = tmp_path / "reports"
    first = _sample_report()
    second = _sample_report()
    second.git = GitSnapshot(
        branch="feature", commits=[], modified=[], untracked=[], staged=[], diff_stat="", error=None
    )

    p1 = first.write_to(report_dir)
    p2 = second.write_to(report_dir)

    assert p1 == p2
    assert p1.exists()
    content = p1.read_text(encoding="utf-8")
    # Second write won — branch replaced, no stale "main" left.
    assert "`feature`" in content
    assert "`main`" not in content


# ---------------------------------------------------------------------------
# build_payload_runner dispatcher
# ---------------------------------------------------------------------------


async def test_dispatcher_echoes_unarmed_payload(tmp_path):
    """Payloads without ``self_evolution`` are echoed — the noop contract."""
    runner_fn = build_payload_runner(str(tmp_path), report_dir=tmp_path / "reports")
    result = await runner_fn({"schedule": "create", "name": "unrelated"})
    assert result == {"ok": True, "echo": {"schedule": "create", "name": "unrelated"}}


async def test_dispatcher_collects_when_armed(monkeypatch, tmp_path):
    """Armed payload runs ``run_once`` and returns a summary + written path."""
    fake_report = _sample_report(cwd=str(tmp_path))
    monkeypatch.setattr(payload_mod, "run_once", AsyncMock(return_value=fake_report))

    report_dir = tmp_path / "reports"
    runner_fn = build_payload_runner(str(tmp_path), report_dir=report_dir)
    result = await runner_fn({"self_evolution": True, "cwd": str(tmp_path)})

    assert result["ok"] is True
    assert result["date"] == _DATE
    assert result["commits"] == 1
    assert result["modified"] == 1
    assert result["ruff_returncode"] == 0
    assert result["pytest_returncode"] == 0
    assert Path(result["path"]).name == f"{_DATE}.md"
    assert Path(result["path"]).exists()


async def test_dispatcher_returns_error_key_on_collection_failure(monkeypatch, tmp_path):
    """An unexpected ``run_once`` failure surfaces as an ``error`` key (not a raise).

    The scheduler's ``_fire`` checks for this key to mark the task row
    ``failed`` instead of crashing the executor thread.
    """
    monkeypatch.setattr(
        payload_mod, "run_once", AsyncMock(side_effect=RuntimeError("boom"))
    )
    runner_fn = build_payload_runner(str(tmp_path), report_dir=tmp_path / "reports")
    result = await runner_fn({"self_evolution": True})

    assert result["ok"] is False
    assert "boom" in result["error"]


async def test_dispatcher_noop_matches_scheduler_noop_byte_for_byte():
    """Zero-regression guarantee: the unarmed path ≡ ``_noop_runner``.

    Wiring the dispatcher in front of every scheduled job is safe only
    because, for ordinary payloads, it returns the exact dict the
    scheduler's default ``_noop_runner`` would have returned. This test
    pins that invariant so a future refactor cannot silently drift.
    """
    runner_fn = build_payload_runner("/tmp/x")
    body = {"any": "schedule", "job": 1, "nested": {"k": [1, 2]}}
    dispatched = await runner_fn(body)
    noop = await _noop_runner(body)
    assert dispatched == noop
