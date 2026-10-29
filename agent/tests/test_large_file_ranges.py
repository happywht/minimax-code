"""Regression coverage for line-range reads beyond the initial byte window."""

from pathlib import Path

import pytest

from minimax_code.agent.tools import get_default_registry


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(tmp_path))
    return tmp_path


async def test_range_after_prefix_is_returned_without_a_cas_hash(workspace: Path) -> None:
    (workspace / "large.txt").write_text("first\n" * 300 + "target\nlast\n", encoding="utf-8")
    result = await get_default_registry().dispatch(
        "read_file", {"path": "large.txt", "start_line": 301, "end_line": 301, "max_bytes": 1024}
    )
    assert result.success, result.error
    assert result.output["content"] == "target\n"
    assert result.output["truncated"] is False
    assert result.output["sha256"] is None


async def test_range_limits_long_line_and_rejects_out_of_range(workspace: Path) -> None:
    (workspace / "large.txt").write_text("x" * 3000 + "\nend\n", encoding="utf-8")
    registry = get_default_registry()
    limited = await registry.dispatch(
        "read_file", {"path": "large.txt", "start_line": 1, "max_bytes": 1024}
    )
    assert limited.success, limited.error
    assert len(limited.output["content"].encode("utf-8")) == 1024
    assert limited.output["truncated"] is True
    missing = await registry.dispatch(
        "read_file", {"path": "large.txt", "start_line": 3, "max_bytes": 1024}
    )
    assert not missing.success
