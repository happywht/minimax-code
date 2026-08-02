"""Tests for v0.11.0 Milestone 1 patch workflow enhancements."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest

from minimax_code.config import Config
from minimax_code.ipc.handlers_patch import register_patch_handlers
from minimax_code.ipc.protocol import INVALID_PARAMS
from minimax_code.ipc.server import IPCServer


def _git_available() -> bool:
    return shutil.which("git") is not None


pytestmark = pytest.mark.skipif(not _git_available(), reason="git binary required for these tests")

GIT_ERROR = -32000
_NOT_A_REPO_CEILING = Path(tempfile.mkdtemp(prefix="patch_not_a_repo_"))


class _CapturingContext:
    def __init__(self) -> None:
        self.reply_payload: Any = None
        self.reply_error_payload: tuple[int, str, Any] | None = None

    async def reply(self, result: Any) -> None:
        self.reply_payload = result

    async def reply_error(self, code: int, message: str, data: Any = None) -> None:
        self.reply_error_payload = (code, message, data)

    async def emit(self, event: str, data: Any = None) -> None:  # pragma: no cover
        return None


@pytest.fixture(autouse=True)
def _git_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(_NOT_A_REPO_CEILING))


def _run_git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "GIT_PAGER": "cat",
        "GIT_CEILING_DIRECTORIES": str(repo.parent),
    }
    out = subprocess.check_output(["git", *args], cwd=str(repo), env=env)
    return out.decode("utf-8", errors="replace")


def _make_handler(
    method: str,
) -> tuple[Callable[..., Coroutine[Any, Any, None]], _CapturingContext]:
    server = IPCServer(config=Config.from_env(), stdin=sys.stdin, stdout=sys.stdout)
    register_patch_handlers(server)
    return server._handlers[method], _CapturingContext()  # type: ignore[return-value]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo with two committed files ready for patch workflows."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "--initial-branch=main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")

    (repo / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (repo / "b.py").write_text("def b():\n    return 1\n", encoding="utf-8")
    _run_git(repo, "add", "a.py", "b.py")
    _run_git(repo, "commit", "-m", "initial")
    return repo


def _write_working_edits(repo: Path, edits: dict[str, str]) -> None:
    """Write ``edits`` to the working tree so ``patch.apply_*`` can read the diff."""
    for name, content in edits.items():
        (repo / name).write_text(content, encoding="utf-8")


@pytest.mark.asyncio
async def test_apply_file_success(repo: Path) -> None:
    edits = {"a.py": "def a():\n    return 2\n"}
    _write_working_edits(repo, edits)

    handler, ctx = _make_handler("patch.apply_file")
    await handler({"cwd": str(repo), "scope": "working", "file_path": "a.py"}, ctx)

    assert ctx.reply_error_payload is None, ctx.reply_error_payload
    payload = ctx.reply_payload
    assert payload == {
        "ok": True,
        "operation": "apply_file",
        "scope": "working",
        "file_path": "a.py",
    }
    assert (repo / "a.py").read_text(encoding="utf-8") == edits["a.py"]
    assert (repo / "b.py").read_text(encoding="utf-8") == "def b():\n    return 1\n"


@pytest.mark.asyncio
async def test_apply_all_success(repo: Path) -> None:
    edits = {"a.py": "def a():\n    return 2\n", "b.py": "def b():\n    return 2\n"}
    _write_working_edits(repo, edits)

    handler, ctx = _make_handler("patch.apply_all")
    await handler({"cwd": str(repo), "scope": "working"}, ctx)

    assert ctx.reply_error_payload is None, ctx.reply_error_payload
    payload = ctx.reply_payload
    assert payload["ok"] is True
    assert payload["operation"] == "apply_all"
    assert payload["scope"] == "working"
    assert sorted(payload["applied"]) == ["a.py", "b.py"]
    assert payload["failed"] == []
    assert (repo / "a.py").read_text(encoding="utf-8") == edits["a.py"]
    assert (repo / "b.py").read_text(encoding="utf-8") == edits["b.py"]


@pytest.mark.asyncio
async def test_save_snapshot_returns_null_when_clean(repo: Path) -> None:
    handler, ctx = _make_handler("patch.save_snapshot")
    await handler({"cwd": str(repo)}, ctx)

    assert ctx.reply_error_payload is None, ctx.reply_error_payload
    assert ctx.reply_payload == {
        "ok": True,
        "snapshot_ref": None,
        "clean": True,
    }


@pytest.mark.asyncio
async def test_save_snapshot_returns_stash_ref_when_dirty(repo: Path) -> None:
    (repo / "a.py").write_text("def a():\n    return 2\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("hello\n", encoding="utf-8")

    handler, ctx = _make_handler("patch.save_snapshot")
    await handler({"cwd": str(repo)}, ctx)

    assert ctx.reply_error_payload is None, ctx.reply_error_payload
    payload = ctx.reply_payload
    assert payload["ok"] is True
    assert payload["clean"] is False
    assert isinstance(payload["snapshot_ref"], str) and payload["snapshot_ref"]

    # The stash should have captured both tracked and untracked changes.
    status = _run_git(repo, "status", "--porcelain")
    assert not status.strip()
    stash_list = _run_git(repo, "stash", "list")
    assert "MiniMax Code patch snapshot" in stash_list


@pytest.mark.asyncio
async def test_apply_file_missing_file_errors() -> None:
    """Requesting a file that is not part of the current diff returns INVALID_PARAMS."""
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="patch_missing_file_"))
    try:
        _run_git(tmp, "init", "--initial-branch=main")
        _run_git(tmp, "config", "user.email", "test@example.com")
        _run_git(tmp, "config", "user.name", "Test")
        (tmp / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
        _run_git(tmp, "add", "a.py")
        _run_git(tmp, "commit", "-m", "initial")

        handler, ctx = _make_handler("patch.apply_file")
        await handler({"cwd": str(tmp), "scope": "working", "file_path": "missing.py"}, ctx)

        assert ctx.reply_payload is None
        assert ctx.reply_error_payload is not None
        code, message, _ = ctx.reply_error_payload
        assert code == INVALID_PARAMS
        assert "not found" in message.lower()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_revert_file_success(repo: Path) -> None:
    edits = {"a.py": "def a():\n    return 2\n"}
    _write_working_edits(repo, edits)

    handler, ctx = _make_handler("patch.revert_file")
    await handler({"cwd": str(repo), "scope": "working", "file_path": "a.py"}, ctx)

    assert ctx.reply_error_payload is None, ctx.reply_error_payload
    payload = ctx.reply_payload
    assert payload == {
        "ok": True,
        "operation": "revert_file",
        "scope": "working",
        "file_path": "a.py",
    }
    assert (repo / "a.py").read_text(encoding="utf-8") == "def a():\n    return 1\n"


@pytest.mark.asyncio
async def test_revert_all_success(repo: Path) -> None:
    edits = {"a.py": "def a():\n    return 2\n", "b.py": "def b():\n    return 2\n"}
    _write_working_edits(repo, edits)

    handler, ctx = _make_handler("patch.revert_all")
    await handler({"cwd": str(repo), "scope": "working"}, ctx)

    assert ctx.reply_error_payload is None, ctx.reply_error_payload
    payload = ctx.reply_payload
    assert payload["ok"] is True
    assert payload["operation"] == "revert_all"
    assert payload["scope"] == "working"
    assert sorted(payload["applied"]) == ["a.py", "b.py"]
    assert payload["failed"] == []
    assert (repo / "a.py").read_text(encoding="utf-8") == "def a():\n    return 1\n"
    assert (repo / "b.py").read_text(encoding="utf-8") == "def b():\n    return 1\n"
