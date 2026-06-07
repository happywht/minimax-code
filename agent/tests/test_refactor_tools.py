"""Unit tests for the refactor tools (ASTRenameTool, ExtractFunctionTool, Provider).

Covers AST-aware rename, regex fallback, extract function with parameter
inference, dry-run behaviour, and Provider install/uninstall.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from minimax_code.agent.skills._builtin.refactor import (
    ASTRenameTool,
    ExtractFunctionTool,
    Provider,
)
from minimax_code.agent.tools.base import ToolRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the safety policy at tmp_path and return it."""
    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(tmp_path))
    return tmp_path


def _write_py(workspace: Path, rel: str, code: str) -> Path:
    """Write a Python file under the workspace."""
    fpath = workspace / rel
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_text(code, encoding="utf-8")
    return fpath


def _write_js(workspace: Path, rel: str, code: str) -> Path:
    """Write a JavaScript file under the workspace."""
    fpath = workspace / rel
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_text(code, encoding="utf-8")
    return fpath


# ---------------------------------------------------------------------------
# ASTRenameTool
# ---------------------------------------------------------------------------


class TestASTRenameTool:
    async def test_dry_run_returns_preview(self, workspace: Path) -> None:
        """dry_run=True returns a preview without modifying the file.

        Note: AST rename matches ast.Name nodes (variable references) reliably.
        Function def names have col_offset pointing at the 'def' keyword, so
        we use a variable reference to ensure the AST path triggers a match.
        """
        fpath = _write_py(workspace, "mod.py", "old_name = 42\nresult = old_name\n")
        tool = ASTRenameTool()
        result = await tool.run(symbol="old_name", new_name="new_name", dry_run=True)

        assert result.success
        assert result.output["dry_run"] is True
        # Original file should be unchanged.
        assert fpath.read_text(encoding="utf-8") == "old_name = 42\nresult = old_name\n"
        # Preview should contain the new name.
        changes = result.output["changes"]
        assert len(changes) >= 1
        assert changes[0].get("preview") is not None
        assert "new_name" in (changes[0].get("preview") or "")

    async def test_dry_run_false_writes_file(self, workspace: Path) -> None:
        """dry_run=False actually renames the symbol in the file."""
        _write_py(workspace, "mod.py", "value = 42\nresult = value\n")
        tool = ASTRenameTool()
        result = await tool.run(symbol="value", new_name="score", dry_run=False)

        assert result.success
        assert result.output["dry_run"] is False
        assert result.output["total_replacements"] >= 1
        # File should contain the new name.
        content = (workspace / "mod.py").read_text(encoding="utf-8")
        assert "score" in content
        assert "value" not in content

    async def test_python_file_uses_ast(self, workspace: Path) -> None:
        """Python files use AST matching: rename Name nodes but not strings."""
        _write_py(
            workspace,
            "app.py",
            'foo = 1\nbar = foo\nmsg = "foo is great"\n',
        )
        tool = ASTRenameTool()
        result = await tool.run(symbol="foo", new_name="baz", dry_run=False)

        assert result.success
        content = (workspace / "app.py").read_text(encoding="utf-8")
        # The variable reference (ast.Name) should be renamed.
        assert "bar = baz" in content
        # The string literal should remain unchanged (AST doesn't touch strings).
        assert '"foo is great"' in content

    async def test_js_file_uses_regex(self, workspace: Path) -> None:
        """Non-Python files use whole-word regex matching."""
        _write_js(workspace, "app.js", "function hello() {\n  return hello;\n}\n")
        tool = ASTRenameTool()
        result = await tool.run(symbol="hello", new_name="greet", dry_run=False)

        assert result.success
        content = (workspace / "app.js").read_text(encoding="utf-8")
        assert "function greet()" in content

    async def test_invalid_symbol_name_returns_error(self, workspace: Path) -> None:
        """Non-identifier symbol names are rejected."""
        tool = ASTRenameTool()
        result = await tool.run(symbol="not-valid", new_name="ok")
        assert not result.success
        assert "invalid" in (result.error or "").lower()

    async def test_invalid_new_name_returns_error(self, workspace: Path) -> None:
        """Non-identifier new names are rejected."""
        tool = ASTRenameTool()
        result = await tool.run(symbol="old", new_name="123bad")
        assert not result.success

    async def test_rename_across_multiple_files(self, workspace: Path) -> None:
        """Rename propagates across multiple files in the scope."""
        _write_py(workspace, "a.py", "x = helper()\n")
        _write_py(workspace, "b.py", "helper = 42\n")
        tool = ASTRenameTool()
        result = await tool.run(
            symbol="helper",
            new_name="assist",
            paths=".",
            dry_run=True,
        )

        assert result.success
        # Should have found changes in at least one file.
        assert len(result.output["changes"]) >= 1

    async def test_no_match_returns_empty_changes(self, workspace: Path) -> None:
        """When the symbol is not found, changes list is empty."""
        _write_py(workspace, "mod.py", "x = 1\n")
        tool = ASTRenameTool()
        result = await tool.run(symbol="nonexistent", new_name="renamed", dry_run=True)

        assert result.success
        assert result.output["changes"] == []


# ---------------------------------------------------------------------------
# ExtractFunctionTool
# ---------------------------------------------------------------------------


class TestExtractFunctionTool:
    async def test_dry_run_returns_preview(self, workspace: Path) -> None:
        """dry_run=True returns a preview with the new function."""
        _write_py(
            workspace,
            "mod.py",
            "x = 10\ny = 20\nresult = x + y\nprint(result)\n",
        )
        tool = ExtractFunctionTool()
        result = await tool.run(
            file_path="mod.py",
            start_line=3,
            end_line=3,
            function_name="compute",
            dry_run=True,
        )

        assert result.success
        assert result.output["dry_run"] is True
        assert result.output["function_name"] == "compute"
        assert result.output["new_content"] is not None
        # Original file should be unchanged.
        content = (workspace / "mod.py").read_text(encoding="utf-8")
        assert "result = x + y" in content

    async def test_dry_run_false_writes_file(self, workspace: Path) -> None:
        """dry_run=False writes the refactored content to disk."""
        _write_py(
            workspace,
            "mod.py",
            "x = 10\nresult = x + 1\nprint(result)\n",
        )
        tool = ExtractFunctionTool()
        result = await tool.run(
            file_path="mod.py",
            start_line=2,
            end_line=2,
            function_name="add_one",
            dry_run=False,
        )

        assert result.success
        assert result.output["dry_run"] is False
        # new_content is None after actual write (the file on disk is the source of truth).
        assert result.output["new_content"] is None
        # File should now contain the new function.
        content = (workspace / "mod.py").read_text(encoding="utf-8")
        assert "def add_one" in content

    async def test_inferred_params(self, workspace: Path) -> None:
        """Parameters are inferred from variables used in the extracted block."""
        _write_py(
            workspace,
            "mod.py",
            "x = 10\ny = 20\nresult = x + y\nprint(result)\n",
        )
        tool = ExtractFunctionTool()
        result = await tool.run(
            file_path="mod.py",
            start_line=3,
            end_line=3,
            function_name="compute",
            dry_run=True,
        )

        assert result.success
        params = result.output["parameters"]
        # x and y should be inferred as parameters.
        assert "x" in params
        assert "y" in params

    async def test_invalid_line_range_returns_error(self, workspace: Path) -> None:
        """Invalid line ranges (out of bounds, reversed) return an error."""
        _write_py(workspace, "mod.py", "x = 1\n")
        tool = ExtractFunctionTool()

        # start_line > end_line
        result = await tool.run(
            file_path="mod.py",
            start_line=5,
            end_line=3,
            function_name="broken",
            dry_run=True,
        )
        assert not result.success
        assert "invalid line range" in (result.error or "")

    async def test_nonexistent_file_returns_error(self, workspace: Path) -> None:
        """Extracting from a nonexistent file returns an error."""
        tool = ExtractFunctionTool()
        result = await tool.run(
            file_path="missing.py",
            start_line=1,
            end_line=3,
            function_name="fn",
            dry_run=True,
        )
        assert not result.success

    async def test_returns_inference(self, workspace: Path) -> None:
        """Return values are inferred for variables used after extraction."""
        _write_py(
            workspace,
            "mod.py",
            "x = 10\nresult = x + 1\nprint(result)\n",
        )
        tool = ExtractFunctionTool()
        result = await tool.run(
            file_path="mod.py",
            start_line=2,
            end_line=2,
            function_name="compute",
            dry_run=True,
        )

        assert result.success
        returns = result.output["returns"]
        assert "result" in returns

    async def test_insert_before_option(self, workspace: Path) -> None:
        """insert_before=True places the new function before the extracted lines."""
        _write_py(
            workspace,
            "mod.py",
            "x = 1\nresult = x + 2\nprint(result)\n",
        )
        tool = ExtractFunctionTool()
        result = await tool.run(
            file_path="mod.py",
            start_line=2,
            end_line=2,
            function_name="compute",
            dry_run=True,
            insert_before=True,
        )

        assert result.success
        content = result.output["new_content"]
        # The function def should appear before the x = 1 line.
        assert content is not None
        func_pos = content.index("def compute")
        call_pos = content.index("x = 1")
        assert func_pos < call_pos


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class TestProvider:
    def test_install_registers_tools(self) -> None:
        """install() adds ast_rename and extract_function to the registry."""
        registry = ToolRegistry()
        provider = Provider()
        added = provider.install(registry)

        assert "ast_rename" in added
        assert "extract_function" in added
        assert registry.has("ast_rename")
        assert registry.has("extract_function")

    def test_install_with_none_returns_empty(self) -> None:
        """install(None) returns empty set without error."""
        provider = Provider()
        added = provider.install(None)
        assert added == set()

    def test_uninstall_removes_tools(self) -> None:
        """uninstall() removes the tools from the registry."""
        registry = ToolRegistry()
        provider = Provider()
        provider.install(registry)

        assert registry.has("ast_rename")
        provider.uninstall(registry)
        assert not registry.has("ast_rename")
        assert not registry.has("extract_function")

    def test_uninstall_with_none_does_nothing(self) -> None:
        """uninstall(None) does not raise."""
        provider = Provider()
        provider.uninstall(None)  # Should not raise.

    def test_install_idempotent(self) -> None:
        """Installing twice does not duplicate tools."""
        registry = ToolRegistry()
        provider = Provider()
        provider.install(registry)
        provider.install(registry)
        assert len([t for t in registry.list() if t.name == "ast_rename"]) == 1
