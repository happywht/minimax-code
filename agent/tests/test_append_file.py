"""v1.5.2 append_file regression tests — tail writes under full protection.

The tool inherits every write-safety layer the field reports demanded:
CAS optimistic locking (``expected_sha256``), the in-flight
concurrent-writer advisory, BackupManager snapshots, and per-run
sandbox redirection. One layer is append-specific and correctness
critical: the sandbox **mirror seeding**. ``redirect_write_target``
snapshots the original into ``_base/`` but never seeds the mirror, so
an "a"-mode open on a fresh mirror would start from empty — and the
eventual collect's three-way compare would overwrite the workspace
with a file that silently lost its pre-append content. These tests pin
the seed, plus the end-to-end collect after a sandboxed append.

All tool exercises go through ``registry.dispatch`` (project
convention since v1.4.1).
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from minimax_code.agent.tools import get_default_registry
from minimax_code.agent.tools.file_ops import _INFLIGHT_WRITES
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


@pytest.fixture(autouse=True)
def clean_inflight():
    _INFLIGHT_WRITES.clear()
    yield
    _INFLIGHT_WRITES.clear()


@contextmanager
def run_as(run_id: str | None):
    """Publish a run id (None = main agent) for the block; reset after."""
    token = set_current_run_id(run_id)
    try:
        yield
    finally:
        reset_current_run_id(token)


@contextmanager
def sandbox_as(root: Path, run_id: str):
    """Publish a per-run sandbox directory for the block; reset after."""
    sb = root / ".minimax" / "sandboxes" / run_id
    sb.mkdir(parents=True, exist_ok=True)
    token = set_sandbox(sb)
    try:
        yield sb
    finally:
        reset_sandbox(token)


async def _dispatch(name: str, args: dict) -> Any:
    return await get_default_registry().dispatch(name, args)


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Plain appends (main-agent path, no sandbox)
# ---------------------------------------------------------------------------


async def test_append_to_existing_extends_tail_with_sha_round_trip(
    workspace_root: Path,
) -> None:
    f = workspace_root / "log.txt"
    f.write_bytes(b"first line\n")
    before = _sha_file(f)

    res = await _dispatch(
        "append_file", {"path": "log.txt", "content": "second line\n"}
    )
    assert res.success, res.error
    assert res.output["created"] is False
    assert res.output["previous_sha256"] == before
    assert res.output["appended_bytes"] == len(b"second line\n")
    # Disk bytes are byte-faithful — \n survives Windows translation.
    assert f.read_bytes() == b"first line\nsecond line\n"
    assert res.output["sha256"] == _sha_file(f)
    assert res.output["size_bytes"] == f.stat().st_size


async def test_append_creates_missing_file_with_parents(workspace_root: Path) -> None:
    res = await _dispatch(
        "append_file", {"path": "deep/nested/new.txt", "content": "seed"}
    )
    assert res.success, res.error
    assert res.output["created"] is True
    assert res.output["previous_sha256"] is None
    assert (workspace_root / "deep" / "nested" / "new.txt").read_text(
        encoding="utf-8"
    ) == "seed"


async def test_append_is_verbatim_no_separator_injected(
    workspace_root: Path,
) -> None:
    """The caller owns line breaks — no implicit newline between appends."""
    f = workspace_root / "raw.txt"
    f.write_text("tail-no-newline", encoding="utf-8")
    res = await _dispatch("append_file", {"path": "raw.txt", "content": "+more"})
    assert res.success, res.error
    assert f.read_text(encoding="utf-8") == "tail-no-newline+more"


# ---------------------------------------------------------------------------
# CAS optimistic locking
# ---------------------------------------------------------------------------


async def test_append_cas_mismatch_fails_with_current_sha(
    workspace_root: Path,
) -> None:
    f = workspace_root / "c.txt"
    f.write_bytes(b"v1")
    stale = hashlib.sha256(b"someone else changed me").hexdigest()

    res = await _dispatch(
        "append_file",
        {"path": "c.txt", "content": "tail", "expected_sha256": stale},
    )
    assert not res.success
    assert res.output["file_exists"] is True
    assert res.output["current_sha256"] == _sha_file(f)
    # Blocked append leaves the disk untouched.
    assert f.read_bytes() == b"v1"


async def test_append_cas_match_succeeds(workspace_root: Path) -> None:
    f = workspace_root / "ok.txt"
    f.write_bytes(b"base")
    expected = _sha_file(f)
    res = await _dispatch(
        "append_file",
        {"path": "ok.txt", "content": "+tail", "expected_sha256": expected},
    )
    assert res.success, res.error
    assert f.read_bytes() == b"base+tail"
    # Round-trip: the output sha arms the next lock.
    assert res.output["sha256"] == _sha_bytes(b"base+tail")


async def test_append_cas_on_deleted_file_fails(workspace_root: Path) -> None:
    f = workspace_root / "gone.txt"
    f.write_bytes(b"transient")
    expected = _sha_file(f)
    f.unlink()

    res = await _dispatch(
        "append_file",
        {"path": "gone.txt", "content": "tail", "expected_sha256": expected},
    )
    assert not res.success
    assert res.output["file_exists"] is False


async def test_append_malformed_expected_sha_fails(workspace_root: Path) -> None:
    (workspace_root / "m.txt").write_text("m", encoding="utf-8")
    res = await _dispatch(
        "append_file", {"path": "m.txt", "content": "x", "expected_sha256": "zz"}
    )
    assert not res.success
    assert "64" in res.error


# ---------------------------------------------------------------------------
# Argument & path validation
# ---------------------------------------------------------------------------


async def test_append_rejects_bad_args(workspace_root: Path) -> None:
    bad_path = await _dispatch("append_file", {"path": "", "content": "x"})
    assert not bad_path.success
    bad_content = await _dispatch("append_file", {"path": "a.txt", "content": 123})
    assert not bad_content.success


async def test_append_to_directory_fails(workspace_root: Path) -> None:
    (workspace_root / "adir").mkdir()
    res = await _dispatch("append_file", {"path": "adir", "content": "x"})
    assert not res.success


# ---------------------------------------------------------------------------
# In-flight advisory (v1.5.1 registry)
# ---------------------------------------------------------------------------


async def test_append_rival_run_warns_but_never_blocks(workspace_root: Path) -> None:
    with run_as("run_a"):
        first = await _dispatch("write_file", {"path": "s.txt", "content": "a\n"})
        assert first.success, first.error

    with run_as("run_b"):
        res = await _dispatch("append_file", {"path": "s.txt", "content": "b\n"})
    assert res.success, res.error
    assert res.output["concurrent_writer"] == "run_a"
    assert "run_a" in res.output["warning"]
    assert (workspace_root / "s.txt").read_text(encoding="utf-8") == "a\nb\n"


async def test_append_same_run_no_warning(workspace_root: Path) -> None:
    with run_as("run_a"):
        await _dispatch("write_file", {"path": "own.txt", "content": "1\n"})
        res = await _dispatch("append_file", {"path": "own.txt", "content": "2\n"})
    assert res.success, res.error
    assert "concurrent_writer" not in res.output


async def test_append_existing_takes_backup(workspace_root: Path) -> None:
    (workspace_root / "b.txt").write_text("precious", encoding="utf-8")
    res = await _dispatch("append_file", {"path": "b.txt", "content": "+x"})
    assert res.success, res.error
    assert res.output.get("backup")


# ---------------------------------------------------------------------------
# fs-bus attribution
# ---------------------------------------------------------------------------


async def test_append_emit_carries_run_id_attribute(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[dict[str, Any]] = []

    class FakeBus:
        def emit(self, kind, paths, cause, **kw):
            captured.append({"kind": kind, "cause": cause, **kw})

    monkeypatch.setattr("minimax_code.app.ensure_fs_bus", lambda: FakeBus())

    with run_as("run_x"):
        res = await _dispatch("append_file", {"path": "e.txt", "content": "x"})
        assert res.success, res.error
    (event,) = captured
    assert event["cause"] == "append_file"
    assert event["kind"] == "created"
    assert event["run_id"] == "run_x"


# ---------------------------------------------------------------------------
# Sandbox: mirror seeding (the append-specific correctness step)
# ---------------------------------------------------------------------------


async def test_sandbox_append_seeds_mirror_with_original(workspace_root: Path) -> None:
    """The data-loss pin: the mirror must contain original + appended.

    Without the seed, "a"-mode would open a fresh empty mirror; the
    workspace original then stays in ``_base`` only, and a later
    collect would happily overwrite the workspace with the truncated
    mirror (three-way compare sees workspace == base → merge).
    """
    (workspace_root / "doc.md").write_text("# Original\nfull body\n", encoding="utf-8")
    original_bytes = (workspace_root / "doc.md").read_bytes()

    with sandbox_as(workspace_root, "run_seed"), run_as("run_seed"):
        res = await _dispatch(
            "append_file", {"path": "doc.md", "content": "\n# Appended tail\n"}
        )
        assert res.success, res.error
        assert res.output["sandboxed"] is True
        assert "sandbox_path" in res.output

        mirror = workspace_root / ".minimax" / "sandboxes" / "run_seed" / "doc.md"
        assert mirror.read_bytes() == original_bytes + b"\n# Appended tail\n"
        # The COW baseline still holds the untouched original.
        base = (
            workspace_root / ".minimax" / "sandboxes" / "run_seed" / "_base" / "doc.md"
        )
        assert base.read_bytes() == original_bytes

    # Workspace untouched until collect.
    assert (workspace_root / "doc.md").read_bytes() == original_bytes


async def test_sandbox_append_new_file_needs_no_seed(workspace_root: Path) -> None:
    with sandbox_as(workspace_root, "run_new"), run_as("run_new"):
        res = await _dispatch("append_file", {"path": "fresh.txt", "content": "one"})
        assert res.success, res.error
        assert res.output["created"] is True
        assert res.output["sandboxed"] is True
    assert not (workspace_root / "fresh.txt").exists()
    mirror = workspace_root / ".minimax" / "sandboxes" / "run_new" / "fresh.txt"
    assert mirror.read_text(encoding="utf-8") == "one"


async def test_sandbox_append_visible_through_overlay_read(workspace_root: Path) -> None:
    (workspace_root / "v.txt").write_text("head-", encoding="utf-8")
    with sandbox_as(workspace_root, "run_ov"), run_as("run_ov"):
        res = await _dispatch("append_file", {"path": "v.txt", "content": "tail"})
        assert res.success, res.error
        seen = await _dispatch("read_file", {"path": "v.txt"})
        assert seen.success, seen.error
        assert seen.output["content"] == "head-tail"


async def test_sandbox_append_then_collect_merges_full_content(
    workspace_root: Path,
) -> None:
    """End-to-end over the data-loss pin: collect lands original + tail."""
    (workspace_root / "merge.txt").write_text("alpha\n", encoding="utf-8")

    with sandbox_as(workspace_root, "run_m"), run_as("run_m"):
        res = await _dispatch("append_file", {"path": "merge.txt", "content": "beta\n"})
        assert res.success, res.error

    # Not in flight, sandbox present, workspace untouched since the
    # snapshot → clean merge path.
    collected = await _dispatch("collect_subagent", {"run_id": "run_m"})
    assert collected.success, collected.error
    assert (workspace_root / "merge.txt").read_text(encoding="utf-8") == (
        "alpha\nbeta\n"
    )
    report = collected.output
    assert any(
        m.get("path") == "merge.txt" for m in report.get("merged", [])
    )
