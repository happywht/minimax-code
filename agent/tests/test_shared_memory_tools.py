"""P0-2 shared memory tools (put / get / list) — dispatch-level tests.

Covers the cross-agent handoff contract: workspace-scope visibility,
per-run isolation (two ContextVar run ids never see each other's
scratchpad), the main-agent run-scope refusal, list filtering, and the
corrupt-store quarantine — a broken ``store.json`` is renamed aside so
the incident stays post-mortem instead of being silently rewritten.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from minimax_code.agent.tools.base import ToolResult, get_default_registry
from minimax_code.workspace_ctx import (
    reset_current_root,
    reset_current_run_id,
    set_current_root,
    set_current_run_id,
)


@pytest.fixture
def workspace_root(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    token = set_current_root(root)
    try:
        yield root
    finally:
        reset_current_root(token)


async def _dispatch(name: str, **kwargs) -> ToolResult:
    return await get_default_registry().dispatch(name, kwargs)


def _store_file(root: Path) -> Path:
    return root / ".minimax" / "shared_memory" / "store.json"


# ---------------------------------------------------------------------------
# put / get round-trips
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_get_roundtrip(workspace_root: Path) -> None:
    put = await _dispatch(
        "shared_memory_put", namespace="contract", key="api", value={"v": 2}
    )
    assert put.success, put.error
    assert put.output["stored"] is True
    assert put.output["scope"] == "workspace"

    got = await _dispatch("shared_memory_get", namespace="contract", key="api")
    assert got.success, got.error
    assert got.output["found"] is True
    assert got.output["value"] == {"v": 2}
    assert got.output["scope"] == "workspace"


@pytest.mark.asyncio
async def test_get_missing_returns_default(workspace_root: Path) -> None:
    got = await _dispatch(
        "shared_memory_get",
        namespace="nope",
        key="nope",
        default="fallback",
    )
    assert got.success, got.error
    assert got.output["found"] is False
    assert got.output["value"] == "fallback"


@pytest.mark.asyncio
async def test_put_overwrites_same_key(workspace_root: Path) -> None:
    for v in ("first", "second"):
        res = await _dispatch(
            "shared_memory_put", namespace="plan", key="step", value=v
        )
        assert res.success, res.error
    got = await _dispatch("shared_memory_get", namespace="plan", key="step")
    assert got.output["found"] is True
    assert got.output["value"] == "second"


@pytest.mark.asyncio
async def test_non_serialisable_value_rejected(workspace_root: Path) -> None:
    res = await _dispatch(
        "shared_memory_put", namespace="x", key="y", value={"s": {1, 2}}
    )
    assert not res.success
    assert "not JSON-serialisable" in (res.error or "")


@pytest.mark.asyncio
async def test_invalid_scope_rejected(workspace_root: Path) -> None:
    res = await _dispatch(
        "shared_memory_put", namespace="x", key="y", value=1, scope="global"
    )
    assert not res.success
    assert "scope" in (res.error or "")


# ---------------------------------------------------------------------------
# run-scope isolation (ContextVar run ids)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_scope_requires_run_id(workspace_root: Path) -> None:
    res = await _dispatch(
        "shared_memory_put", namespace="scratch", key="k", value=1, scope="run"
    )
    assert not res.success
    assert "requires a sub-agent run_id" in (res.error or "")


@pytest.mark.asyncio
async def test_run_scope_isolated_between_runs(workspace_root: Path) -> None:
    token_a = set_current_run_id("run_a")
    try:
        put = await _dispatch(
            "shared_memory_put",
            namespace="scratch",
            key="k",
            value="a-only",
            scope="run",
        )
        assert put.success, put.error
        own = await _dispatch(
            "shared_memory_get", namespace="scratch", key="k", scope="run"
        )
        assert own.output["found"] is True
        assert own.output["value"] == "a-only"
    finally:
        reset_current_run_id(token_a)

    token_b = set_current_run_id("run_b")
    try:
        other = await _dispatch(
            "shared_memory_get", namespace="scratch", key="k", scope="run"
        )
        assert other.success, other.error
        assert other.output["found"] is False
    finally:
        reset_current_run_id(token_b)


@pytest.mark.asyncio
async def test_workspace_scope_visible_across_runs(workspace_root: Path) -> None:
    token_a = set_current_run_id("run_a")
    try:
        res = await _dispatch(
            "shared_memory_put", namespace="handoff", key="owner", value="main"
        )
        assert res.success, res.error
    finally:
        reset_current_run_id(token_a)

    token_b = set_current_run_id("run_b")
    try:
        got = await _dispatch("shared_memory_get", namespace="handoff", key="owner")
        assert got.output["found"] is True
        assert got.output["value"] == "main"
    finally:
        reset_current_run_id(token_b)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_filters_namespace_and_scope(workspace_root: Path) -> None:
    for ns, key, scope in (
        ("plan", "a", "workspace"),
        ("plan", "b", "workspace"),
        ("contract", "c", "workspace"),
    ):
        res = await _dispatch(
            "shared_memory_put", namespace=ns, key=key, value=1, scope=scope
        )
        assert res.success, res.error
    token = set_current_run_id("run_l")
    try:
        res = await _dispatch(
            "shared_memory_put", namespace="plan", key="scratch", value=2, scope="run"
        )
        assert res.success, res.error
    finally:
        reset_current_run_id(token)

    all_plan = await _dispatch("shared_memory_list", namespace="plan")
    assert all_plan.success, all_plan.error
    keys = {e["key"] for e in all_plan.output["entries"]}
    # Namespace filtering alone does NOT hide run-scope entries — the
    # per-run isolation filter only applies to an explicit scope="run"
    # call (see the isolation test above).
    assert keys == {"a", "b", "scratch"}

    workspace_only = await _dispatch("shared_memory_list", scope="workspace")
    assert workspace_only.output["count"] == 3  # scratch excluded by scope


# ---------------------------------------------------------------------------
# no-root behaviour (put fails hard, get/list soft-skip)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_root_put_fails_get_soft_skips() -> None:
    put = await _dispatch("shared_memory_put", namespace="x", key="y", value=1)
    assert not put.success
    assert "no workspace root" in (put.error or "")

    got = await _dispatch("shared_memory_get", namespace="x", key="y")
    assert got.success
    assert got.output["found"] is False
    assert got.output["reason"] == "no workspace root"

    listed = await _dispatch("shared_memory_list")
    assert listed.success
    assert listed.output["entries"] == []


# ---------------------------------------------------------------------------
# corrupt store quarantine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_corrupt_store_quarantined_not_rewritten(workspace_root: Path) -> None:
    store = _store_file(workspace_root)
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("{ this is not json", encoding="utf-8")

    got = await _dispatch("shared_memory_get", namespace="any", key="key")
    assert got.success, got.error
    assert got.output["found"] is False
    # The wreck was renamed aside, not silently replaced.
    quarantined = list(store.parent.glob("store.json.corrupt-*"))
    assert len(quarantined) == 1
    assert not store.exists()

    # The store self-heals on the next write.
    put = await _dispatch(
        "shared_memory_put", namespace="fresh", key="start", value=True
    )
    assert put.success, put.error
    got2 = await _dispatch("shared_memory_get", namespace="fresh", key="start")
    assert got2.output["found"] is True
