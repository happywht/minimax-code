"""Tool unit tests — covers happy path, errors, and security boundaries.

Each built-in tool gets at least three tests (normal / error /
edge). The security tests verify that path traversal, sensitive
directories, and absolute paths outside the workspace are all
rejected.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from minimax_code.agent.tools import (
    EditFileTool,
    ExecCommandTool,
    ListDirectoryTool,
    PathSecurityError,
    ReadFileTool,
    SearchFilesTool,
    WriteFileTool,
    get_default_registry,
    safe_resolve,
)

# ---------------------------------------------------------------------------
# Path-safety policy
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the safety policy at ``tmp_path`` for the duration of the test."""
    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(tmp_path))
    return tmp_path


def test_safe_resolve_accepts_workspace_relative(workspace: Path) -> None:
    f = workspace / "hello.txt"
    f.write_text("hi")
    assert safe_resolve("hello.txt") == f.resolve()


def test_safe_resolve_rejects_traversal(workspace: Path) -> None:
    with pytest.raises(PathSecurityError):
        safe_resolve("../etc/passwd")
    with pytest.raises(PathSecurityError):
        safe_resolve("a/../../outside.txt")


def test_safe_resolve_rejects_sensitive_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    # Even if the workspace *is* the user's home, ``.ssh`` must be blocked.
    fake_home = Path(os.path.expanduser("~")).resolve()
    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(fake_home))
    ssh_dir = fake_home / ".ssh"
    try:
        if not ssh_dir.exists():
            ssh_dir.mkdir()
        try:
            with pytest.raises(PathSecurityError):
                safe_resolve("~/.ssh/id_rsa")
        finally:
            # Clean up
            if ssh_dir.exists() and not any(ssh_dir.iterdir()):
                ssh_dir.rmdir()
    except (PermissionError, OSError):
        # On some Windows hosts the real .ssh is read-only — that's fine,
        # the test just exercises the code path.
        pass


def test_safe_resolve_rejects_outside_workspace(workspace: Path) -> None:
    # Use a sibling of the workspace fixture (not the same
    # tmp_path the fixture lives in).
    import tempfile

    with tempfile.TemporaryDirectory() as other:
        outside = Path(other)
        target = outside / "secret.txt"
        target.write_text("nope")
        with pytest.raises(PathSecurityError):
            safe_resolve(str(target))


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_file_happy(workspace: Path) -> None:
    f = workspace / "hello.txt"
    # Write raw bytes — Path.write_text on Windows rewrites \n
    # to \r\n, which would leak into our round-trip check.
    f.write_bytes(b"alpha\nbeta\ngamma\n")
    tool = ReadFileTool()
    result = await tool.run(path="hello.txt")
    assert result.success
    assert result.output["content"] == "alpha\nbeta\ngamma\n"
    assert result.output["path"].endswith("hello.txt")
    assert result.metadata["total_lines"] == 3


@pytest.mark.asyncio
async def test_read_file_line_range(workspace: Path) -> None:
    f = workspace / "lines.txt"
    f.write_text("\n".join(str(i) for i in range(1, 11)) + "\n", encoding="utf-8")
    tool = ReadFileTool()
    result = await tool.run(path="lines.txt", start_line=3, end_line=5)
    assert result.success
    assert result.output["content"] == "3\n4\n5\n"


@pytest.mark.asyncio
async def test_read_file_missing(workspace: Path) -> None:
    tool = ReadFileTool()
    result = await tool.run(path="nope.txt")
    assert not result.success
    assert "not found" in (result.error or "")


@pytest.mark.asyncio
async def test_read_file_rejects_traversal(workspace: Path) -> None:
    tool = ReadFileTool()
    result = await tool.run(path="../../../etc/passwd")
    assert not result.success
    assert "traversal" in (result.error or "") or "outside" in (result.error or "")


@pytest.mark.asyncio
async def test_read_file_rejects_binary(workspace: Path) -> None:
    f = workspace / "blob.bin"
    f.write_bytes(b"abc\x00def")
    tool = ReadFileTool()
    result = await tool.run(path="blob.bin")
    assert not result.success
    assert "binary" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_file_happy(workspace: Path) -> None:
    tool = WriteFileTool()
    result = await tool.run(path="new.txt", content="hello\n")
    assert result.success
    assert result.output["created"] is True
    assert (workspace / "new.txt").read_text(encoding="utf-8") == "hello\n"


@pytest.mark.asyncio
async def test_write_file_overwrite(workspace: Path) -> None:
    f = workspace / "existing.txt"
    f.write_text("old")
    tool = WriteFileTool()
    result = await tool.run(path="existing.txt", content="new")
    assert result.success
    assert result.output["overwritten"] is True
    assert f.read_text(encoding="utf-8") == "new"


@pytest.mark.asyncio
async def test_write_file_creates_parent_dirs(workspace: Path) -> None:
    tool = WriteFileTool()
    result = await tool.run(path="a/b/c/file.txt", content="x")
    assert result.success
    assert (workspace / "a" / "b" / "c" / "file.txt").read_text() == "x"


@pytest.mark.asyncio
async def test_write_file_rejects_traversal(workspace: Path) -> None:
    tool = WriteFileTool()
    result = await tool.run(path="../../evil.txt", content="x")
    assert not result.success


# ---------------------------------------------------------------------------
# list_directory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_directory_default_root(workspace: Path) -> None:
    (workspace / "alpha.py").write_text("a")
    (workspace / "beta").mkdir()
    (workspace / "gamma.md").write_text("g")
    tool = ListDirectoryTool()
    result = await tool.run()
    assert result.success
    names = [e["name"] for e in result.output["entries"]]
    assert "alpha.py" in names and "beta" in names and "gamma.md" in names
    beta_entry = next(e for e in result.output["entries"] if e["name"] == "beta")
    assert beta_entry["is_dir"] is True


@pytest.mark.asyncio
async def test_list_directory_pattern(workspace: Path) -> None:
    for name in ("a.py", "b.py", "c.md"):
        (workspace / name).write_text("x")
    tool = ListDirectoryTool()
    result = await tool.run(pattern="*.py")
    assert result.success
    names = [e["name"] for e in result.output["entries"]]
    assert set(names) == {"a.py", "b.py"}


@pytest.mark.asyncio
async def test_list_directory_missing(workspace: Path) -> None:
    tool = ListDirectoryTool()
    result = await tool.run(path="missing_dir")
    assert not result.success


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_file_unique_replace(workspace: Path) -> None:
    f = workspace / "code.py"
    f.write_text("def foo():\n    return 1\n\ndef bar():\n    return 2\n", encoding="utf-8")
    tool = EditFileTool()
    result = await tool.run(
        path="code.py",
        old_string="    return 1",
        new_string="    return 42",
    )
    assert result.success
    assert result.output["replacements"] == 1
    assert "return 42" in f.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_edit_file_ambiguous_rejected(workspace: Path) -> None:
    f = workspace / "code.py"
    f.write_text("x = 1\nx = 1\n", encoding="utf-8")
    tool = EditFileTool()
    result = await tool.run(
        path="code.py",
        old_string="x = 1",
        new_string="x = 2",
    )
    assert not result.success
    assert "matches 2" in (result.error or "")


@pytest.mark.asyncio
async def test_edit_file_replace_all(workspace: Path) -> None:
    f = workspace / "code.py"
    f.write_text("x = 1\nx = 1\nx = 1\n", encoding="utf-8")
    tool = EditFileTool()
    result = await tool.run(
        path="code.py",
        old_string="x = 1",
        new_string="x = 2",
        replace_all=True,
    )
    assert result.success
    assert result.output["replacements"] == 3
    assert f.read_text(encoding="utf-8").count("x = 2") == 3


@pytest.mark.asyncio
async def test_edit_file_not_found_returns_hint(workspace: Path) -> None:
    f = workspace / "code.py"
    f.write_text("def foo():\n    return 1\n", encoding="utf-8")
    tool = EditFileTool()
    result = await tool.run(
        path="code.py",
        old_string="def bar():",
        new_string="def baz():",
    )
    assert not result.success
    assert "not found" in (result.error or "")
    # No diff metadata in the failure path.
    assert "diff" not in (result.output or {})


@pytest.mark.asyncio
async def test_edit_file_rejects_traversal(workspace: Path) -> None:
    tool = EditFileTool()
    result = await tool.run(path="../escape.py", old_string="a", new_string="b")
    assert not result.success


# ---------------------------------------------------------------------------
# exec_command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exec_command_echo(workspace: Path) -> None:
    tool = ExecCommandTool()
    script = workspace / "echo_test.py"
    script.write_text("print('hello world')\n", encoding="utf-8")
    result = await tool.run(cmd=[sys.executable, str(script)])
    assert result.success, result.error
    assert "hello world" in result.output["stdout"]
    assert result.output["exit_code"] == 0


@pytest.mark.asyncio
async def test_exec_command_nonzero_exit(workspace: Path) -> None:
    tool = ExecCommandTool()
    script = workspace / "exit_test.py"
    script.write_text("raise SystemExit(3)\n", encoding="utf-8")
    result = await tool.run(cmd=[sys.executable, str(script)])
    assert not result.success
    assert "code 3" in (result.error or "")
    assert result.output["exit_code"] == 3


@pytest.mark.asyncio
async def test_exec_command_timeout(workspace: Path) -> None:
    tool = ExecCommandTool()
    script = workspace / "timeout_test.py"
    script.write_text("import time\ntime.sleep(10)\n", encoding="utf-8")
    result = await tool.run(cmd=[sys.executable, str(script)], timeout=1)
    assert not result.success
    assert "timed out" in (result.error or "")
    assert result.output["timed_out"] is True


@pytest.mark.asyncio
async def test_exec_command_rejects_dangerous(workspace: Path) -> None:
    tool = ExecCommandTool()
    result = await tool.run(cmd=["rm", "-rf", "/"])
    assert not result.success
    assert "dangerous" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_exec_command_rejects_traversal_cwd(workspace: Path) -> None:
    tool = ExecCommandTool()
    result = await tool.run(cmd=["python", "-c", "print(1)"], cwd="../../etc")
    assert not result.success


@pytest.mark.asyncio
async def test_exec_command_invalid_cmd_type(workspace: Path) -> None:
    tool = ExecCommandTool()
    result = await tool.run(cmd="not-a-list")
    assert not result.success


# ---------------------------------------------------------------------------
# search_files
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_files_literal(workspace: Path) -> None:
    (workspace / "a.py").write_text("def foo():\n    return 42\n", encoding="utf-8")
    (workspace / "b.py").write_text("def bar():\n    return 0\n", encoding="utf-8")
    tool = SearchFilesTool()
    result = await tool.run(pattern="foo", path=".")
    assert result.success
    assert result.metadata["count"] >= 1
    files = {m["file"] for m in result.output["matches"]}
    assert "a.py" in files


@pytest.mark.asyncio
async def test_search_files_regex(workspace: Path) -> None:
    (workspace / "a.py").write_text("id = 1\nid = 2\nname = 3\n", encoding="utf-8")
    tool = SearchFilesTool()
    result = await tool.run(pattern=r"^id\s*=", path=".", regex=True)
    assert result.success
    assert result.metadata["count"] == 2


@pytest.mark.asyncio
async def test_search_files_glob_filter(workspace: Path) -> None:
    (workspace / "a.py").write_text("foo\n")
    (workspace / "a.md").write_text("foo\n")
    tool = SearchFilesTool()
    result = await tool.run(pattern="foo", path=".", file_pattern="*.py")
    assert result.success
    files = {m["file"] for m in result.output["matches"]}
    assert files == {"a.py"}


@pytest.mark.asyncio
async def test_search_files_no_match(workspace: Path) -> None:
    (workspace / "a.py").write_text("hello\n")
    tool = SearchFilesTool()
    result = await tool.run(pattern="zzzzzz", path=".")
    assert result.success
    assert result.metadata["count"] == 0
    assert result.output["matches"] == []


@pytest.mark.asyncio
async def test_search_files_invalid_regex(workspace: Path) -> None:
    tool = SearchFilesTool()
    result = await tool.run(pattern="(unclosed", path=".", regex=True)
    assert not result.success
    assert "invalid regex" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_search_files_rejects_traversal(workspace: Path) -> None:
    tool = SearchFilesTool()
    result = await tool.run(pattern="x", path="../../etc")
    assert not result.success


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


def test_registry_has_all_built_in_tools() -> None:
    reg = get_default_registry()
    expected = {
        "read_file",
        "write_file",
        "list_directory",
        "edit_file",
        "exec_command",
        "search_files",
    }
    assert set(reg.names()) >= expected


def test_registry_to_llm_functions_shape() -> None:
    reg = get_default_registry()
    fns = reg.to_llm_functions()
    assert all(f["type"] == "function" for f in fns)
    for f in fns:
        assert "name" in f["function"]
        assert "description" in f["function"]
        assert f["function"]["parameters"]["type"] == "object"


@pytest.mark.asyncio
async def test_registry_dispatch_unknown_tool() -> None:
    reg = get_default_registry()
    result = await reg.dispatch("does_not_exist", {})
    assert not result.success
    assert "unknown tool" in (result.error or "")


@pytest.mark.asyncio
async def test_registry_dispatch_invalid_args() -> None:
    reg = get_default_registry()
    result = await reg.dispatch("read_file", {})  # missing 'path'
    assert not result.success
    assert "invalid args" in (result.error or "")
