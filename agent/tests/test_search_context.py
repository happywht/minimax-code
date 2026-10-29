from pathlib import Path

from minimax_code.agent.tools import get_default_registry
from minimax_code.workspace_ctx import reset_current_root, reset_sandbox, set_current_root, set_sandbox


async def test_context_search_across_files_and_limits(tmp_path: Path) -> None:
    token = set_current_root(tmp_path)
    try:
        (tmp_path / "a.py").write_text("before\nneedle\nafter\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("needle\nlast\n", encoding="utf-8")
        registry = get_default_registry()
        result = await registry.dispatch("search_files", {"pattern": "needle", "context_lines": 1})
        assert result.success
        matches = {m["file"]: m for m in result.output["matches"]}
        assert matches["a.py"]["before"] == [{"line": 1, "text": "before"}]
        assert matches["a.py"]["after"] == [{"line": 3, "text": "after"}]
        assert matches["b.py"]["before"] == []
        assert matches["b.py"]["after"] == [{"line": 2, "text": "last"}]
        for args in ({"context_lines": 6}, {"context_lines": 1, "max_results": 201}):
            rejected = await registry.dispatch("search_files", {"pattern": "needle", **args})
            assert not rejected.success
    finally:
        reset_current_root(token)


async def test_context_search_uses_sandbox_content(tmp_path: Path) -> None:
    root_token = set_current_root(tmp_path)
    sandbox = tmp_path / ".minimax" / "sandboxes" / "run"
    sandbox.mkdir(parents=True)
    (tmp_path / "a.py").write_text("original\nneedle\n", encoding="utf-8")
    (sandbox / "a.py").write_text("modified\nneedle\n", encoding="utf-8")
    sandbox_token = set_sandbox(sandbox)
    try:
        result = await get_default_registry().dispatch(
            "search_files", {"pattern": "needle", "context_lines": 1}
        )
        assert result.success
        assert result.output["matches"][0]["before"] == [{"line": 1, "text": "modified"}]
    finally:
        reset_sandbox(sandbox_token)
        reset_current_root(root_token)
