"""v1.6.0 — collect_subagent: sandbox prune + receipt migration.

v1.5.x collected sandboxes were left on disk forever (unbounded growth)
and wrote their idempotence marker inside the very tree a prune would
delete. v1.6.0 moves receipts to a flat sibling directory
(``.minimax/sandboxes/.collected/<run_id>.json``) and prunes the sandbox
tree after a fully successful collect. These tests pin:

* the prune gate — receipt durability, no conflicts/errors/skips,
  and the explicit ``prune=false`` opt-out
* receipt migration for legacy in-sandbox ``.merged`` markers (a
  pre-upgrade collect must stay recognised, and its marker must move
  to the flat home before the tree is deleted)
* advisory failure handling — an rmtree failure never fails the merge
  and never breaks already-collected idempotence; a later collect
  retries the prune (self-healing)
* schema acceptance of the new ``prune`` parameter through dispatch

All collect exercises go through ``registry.dispatch`` (project
convention since v1.4.1).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from minimax_code.agent.tools import get_default_registry
from minimax_code.agent.tools import sandbox as sandbox_mod
from minimax_code.agent.tools.sandbox import (
    BASE_DIR,
    COLLECTED_DIR,
    MERGED_MARKER,
    SANDBOX_RELPATH,
    collected_marker_path,
    sandbox_files_written,
)
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


async def _dispatch(name: str, args: dict) -> Any:
    return await get_default_registry().dispatch(name, args)


def _stage(root: Path, run_id: str, rel: str, *, sb: bytes) -> Path:
    """Plant one sandbox mirror (no workspace copy, no baseline)."""
    box = root / SANDBOX_RELPATH / run_id
    mirror = box.joinpath(*rel.split("/"))
    mirror.parent.mkdir(parents=True, exist_ok=True)
    mirror.write_bytes(sb)
    return box


def _receipt_path(root: Path, run_id: str) -> Path:
    return root / SANDBOX_RELPATH / COLLECTED_DIR / f"{run_id}.json"


# ---------------------------------------------------------------------------
# Default prune + receipt shape
# ---------------------------------------------------------------------------


async def test_default_prune_removes_tree_and_writes_receipt(
    workspace_root: Path,
) -> None:
    run_id = "run_p1"
    box = _stage(workspace_root, run_id, "a.txt", sb=b"hello")

    res = await _dispatch("collect_subagent", {"run_id": run_id})
    assert res.success, res.error
    assert res.output["pruned"] is True
    assert "prune_error" not in res.output
    assert not box.exists()
    receipt = _receipt_path(workspace_root, run_id)
    assert receipt.is_file()
    # The receipt is the report — enough to replay a later collect.
    saved = json.loads(receipt.read_text(encoding="utf-8"))
    assert saved["run_id"] == run_id
    assert [m["path"] for m in saved["merged"]] == ["a.txt"]
    assert "collected_at" in saved


async def test_second_collect_replays_with_pruned_flag(
    workspace_root: Path,
) -> None:
    run_id = "run_p2"
    _stage(workspace_root, run_id, "a.txt", sb=b"hello")
    first = await _dispatch("collect_subagent", {"run_id": run_id})
    assert first.success, first.error

    second = await _dispatch("collect_subagent", {"run_id": run_id})
    assert second.success, second.error
    assert second.output["already_collected"] is True
    assert second.output["merged"] == first.output["merged"]
    assert second.output["pruned"] is True  # nothing left to prune ≈ success


async def test_prune_false_keeps_tree(workspace_root: Path) -> None:
    run_id = "run_p3"
    box = _stage(workspace_root, run_id, "a.txt", sb=b"hello")

    res = await _dispatch(
        "collect_subagent", {"run_id": run_id, "prune": False}
    )
    assert res.success, res.error
    assert "pruned" not in res.output
    assert box.is_dir()  # explicit opt-out keeps the sandbox
    assert _receipt_path(workspace_root, run_id).is_file()


# ---------------------------------------------------------------------------
# Conservative prune gates
# ---------------------------------------------------------------------------


async def test_conflicts_keep_sandbox(workspace_root: Path) -> None:
    run_id = "run_p4"
    box = _stage(workspace_root, run_id, "shared.txt", sb=b"sandbox")
    ws_file = workspace_root / "shared.txt"
    ws_file.write_bytes(b"external")
    base_file = (box / BASE_DIR) / "shared.txt"
    base_file.parent.mkdir(parents=True, exist_ok=True)
    base_file.write_bytes(b"original")

    res = await _dispatch("collect_subagent", {"run_id": run_id})
    assert not res.success
    assert "pruned" not in res.output
    assert box.is_dir()
    assert not _receipt_path(workspace_root, run_id).exists()


async def test_skipped_files_keep_sandbox(workspace_root: Path) -> None:
    run_id = "run_p5"
    box = _stage(workspace_root, run_id, "shared.txt", sb=b"sandbox")
    (workspace_root / "shared.txt").write_bytes(b"external")
    base_file = (box / BASE_DIR) / "shared.txt"
    base_file.parent.mkdir(parents=True, exist_ok=True)
    base_file.write_bytes(b"original")

    res = await _dispatch(
        "collect_subagent", {"run_id": run_id, "on_conflict": "skip"}
    )
    assert res.success, res.error
    assert res.output["skipped"] == ["shared.txt"]
    assert "pruned" not in res.output
    # The skipped file's only sandbox copy lives in the tree — keep it.
    assert box.is_dir()
    assert _receipt_path(workspace_root, run_id).is_file()


async def test_errors_keep_sandbox(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "run_p6"
    box = _stage(workspace_root, run_id, "a.txt", sb=b"hello")
    # Force the per-file read to fail → the merge reports an error.
    monkeypatch.setattr(
        "minimax_code.agent.tools.file_ops.file_sha256", lambda _p: None
    )

    res = await _dispatch("collect_subagent", {"run_id": run_id})
    assert res.success, res.error
    assert res.output["errors"] == [
        {"path": "a.txt", "error": "sandbox copy is unreadable"}
    ]
    assert "pruned" not in res.output
    assert box.is_dir()
    assert _receipt_path(workspace_root, run_id).is_file()


# ---------------------------------------------------------------------------
# Advisory prune failures + self-healing
# ---------------------------------------------------------------------------


async def test_rmtree_failure_is_advisory_and_idempotent(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "run_p7"
    box = _stage(workspace_root, run_id, "a.txt", sb=b"hello")

    def boom(_path: Path, *args: Any, **kw: Any) -> None:
        raise OSError("locked by another process")

    monkeypatch.setattr(sandbox_mod.shutil, "rmtree", boom)

    first = await _dispatch("collect_subagent", {"run_id": run_id})
    assert first.success, first.error  # the merge itself succeeded
    assert first.output["pruned"] is False
    assert "locked" in first.output["prune_error"]
    assert box.is_dir()
    merged_file = workspace_root / "a.txt"
    stat_after_first = merged_file.stat()

    # Second collect: already-collected replay, prune retried (still
    # failing) — and crucially no re-merge (mtime unchanged).
    second = await _dispatch("collect_subagent", {"run_id": run_id})
    assert second.success, second.error
    assert second.output["already_collected"] is True
    assert second.output["pruned"] is False
    assert "locked" in second.output["prune_error"]
    assert merged_file.stat().st_mtime_ns == stat_after_first.st_mtime_ns


async def test_already_collected_retries_failed_prune(
    workspace_root: Path,
) -> None:
    run_id = "run_p8"
    box = _stage(workspace_root, run_id, "a.txt", sb=b"hello")

    def boom(_path: Path, *args: Any, **kw: Any) -> None:
        raise OSError("locked by another process")

    mp = pytest.MonkeyPatch()
    mp.setattr(sandbox_mod.shutil, "rmtree", boom)
    try:
        first = await _dispatch("collect_subagent", {"run_id": run_id})
        assert first.success, first.error
        assert first.output["pruned"] is False
        assert box.is_dir()
    finally:
        mp.undo()

    # The transient lock is gone — the next collect self-heals the prune.
    second = await _dispatch("collect_subagent", {"run_id": run_id})
    assert second.success, second.error
    assert second.output["already_collected"] is True
    assert second.output["pruned"] is True
    assert not box.exists()


async def test_receipt_write_failure_blocks_prune(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "run_p9"
    box = _stage(workspace_root, run_id, "a.txt", sb=b"hello")
    # A receipt path whose parent is a regular file: mkdir raises.
    blocker = workspace_root / "blocker.txt"
    blocker.write_text("in the way", encoding="utf-8")
    monkeypatch.setattr(
        sandbox_mod, "collected_marker_path", lambda _rid: blocker / "x.json"
    )

    res = await _dispatch("collect_subagent", {"run_id": run_id})
    assert res.success, res.error  # merge worked, receipt did not
    assert "pruned" not in res.output
    assert box.is_dir()  # no receipt ⇒ never delete the evidence


# ---------------------------------------------------------------------------
# Legacy marker migration (v1.5.x compatibility)
# ---------------------------------------------------------------------------


async def test_legacy_marker_recognized_as_collected(
    workspace_root: Path,
) -> None:
    run_id = "run_leg1"
    box = _stage(workspace_root, run_id, "a.txt", sb=b"hello")
    legacy_report = {"run_id": run_id, "merged": [{"path": "a.txt"}]}
    (box / MERGED_MARKER).write_text(
        json.dumps(legacy_report), encoding="utf-8"
    )

    # Recognition only — prune=False leaves the legacy layout untouched.
    res = await _dispatch(
        "collect_subagent", {"run_id": run_id, "prune": False}
    )
    assert res.success, res.error
    assert res.output["already_collected"] is True
    assert res.output["merged"] == [{"path": "a.txt"}]
    assert (box / MERGED_MARKER).is_file()
    assert not _receipt_path(workspace_root, run_id).exists()


async def test_legacy_marker_migrates_then_prunes(
    workspace_root: Path,
) -> None:
    run_id = "run_leg2"
    box = _stage(workspace_root, run_id, "a.txt", sb=b"hello")
    legacy_report = {"run_id": run_id, "merged": [{"path": "a.txt"}]}
    (box / MERGED_MARKER).write_text(
        json.dumps(legacy_report), encoding="utf-8"
    )

    # Default collect: the legacy marker is migrated to the flat home,
    # then the tree (marker included) is pruned.
    res = await _dispatch("collect_subagent", {"run_id": run_id})
    assert res.success, res.error
    assert res.output["already_collected"] is True
    assert res.output["pruned"] is True
    assert not box.exists()
    receipt = _receipt_path(workspace_root, run_id)
    assert receipt.is_file()
    assert json.loads(receipt.read_text(encoding="utf-8"))["merged"] == [
        {"path": "a.txt"}
    ]

    # Idempotence survives the migration — the next collect reads the
    # flat receipt (the legacy marker is gone with the tree).
    again = await _dispatch("collect_subagent", {"run_id": run_id})
    assert again.success, again.error
    assert again.output["already_collected"] is True
    assert again.output["merged"] == [{"path": "a.txt"}]


# ---------------------------------------------------------------------------
# Schema + layout pins
# ---------------------------------------------------------------------------


async def test_dispatch_accepts_prune_parameter(workspace_root: Path) -> None:
    """``additionalProperties: False`` — the schema must know 'prune'."""
    run_id = "run_p10"
    _stage(workspace_root, run_id, "a.txt", sb=b"hello")
    # An unknown parameter would be rejected at dispatch; 'prune' must
    # pass validation and reach the tool.
    res = await _dispatch(
        "collect_subagent", {"run_id": run_id, "prune": False}
    )
    assert res.success, res.error
    assert "pruned" not in res.output  # prune=False honoured end-to-end
    bogus = await _dispatch(
        "collect_subagent", {"run_id": run_id, "bogus_param": 1}
    )
    assert not bogus.success  # unknown params still rejected


def test_collected_marker_path_shape(workspace_root: Path) -> None:
    marker = collected_marker_path("run_abc")
    assert marker is not None
    assert marker == workspace_root / ".minimax" / "sandboxes" / ".collected" / "run_abc.json"


def test_sandbox_files_written_ignores_collected_dir(
    workspace_root: Path,
) -> None:
    """The receipt home must never leak into a sandbox's file manifest."""
    run_id = "run_p11"
    _stage(workspace_root, run_id, "real.txt", sb=b"x")
    other_receipt = (
        workspace_root / SANDBOX_RELPATH / COLLECTED_DIR / "run_p99.json"
    )
    other_receipt.parent.mkdir(parents=True, exist_ok=True)
    other_receipt.write_text("{}", encoding="utf-8")

    assert sandbox_files_written(run_id) == ["real.txt"]
