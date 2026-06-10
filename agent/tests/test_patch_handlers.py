"""Tests for the structured ``patch.preview`` IPC handler."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Coroutine

import pytest

from minimax_code.config import Config
from minimax_code.ipc.handlers_patch import parse_unified_diff, register_patch_handlers
from minimax_code.ipc.server import IPCServer


def _git_available() -> bool:
    return shutil.which("git") is not None


pytestmark = pytest.mark.skipif(
    not _git_available(), reason="git binary required for these tests"
)

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


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "--initial-branch=main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")

    app = repo / "app.py"
    app.write_text("def main():\n    return 1\n", encoding="utf-8")
    _run_git(repo, "add", "app.py")
    _run_git(repo, "commit", "-m", "initial")

    app.write_text(
        "def main():\n    return 2\n\ndef helper():\n    return 3\n",
        encoding="utf-8",
    )
    return repo


def _make_handler(method: str) -> tuple[Callable[..., Coroutine[Any, Any, None]], _CapturingContext]:
    server = IPCServer(config=Config.from_env(), stdin=sys.stdin, stdout=sys.stdout)
    register_patch_handlers(server)
    return server._handlers[method], _CapturingContext()  # type: ignore[return-value]


def test_parse_unified_diff_groups_files_and_hunks() -> None:
    diff = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,2 +1,3 @@ def main\n"
        " line_a\n"
        "-line_b\n"
        "+line_c\n"
        "+line_d\n"
    )
    parsed = parse_unified_diff(diff)
    assert parsed["stats"] == {"files": 1, "additions": 2, "deletions": 1}
    file = parsed["files"][0]
    assert file["path"] == "foo.py"
    assert file["status"] == "modified"
    assert file["hunks"][0]["header"] == "def main"
    assert [line["kind"] for line in file["hunks"][0]["lines"]] == [
        "context",
        "delete",
        "add",
        "add",
    ]


@pytest.mark.asyncio
async def test_patch_preview_returns_structured_working_diff(repo: Path) -> None:
    handler, ctx = _make_handler("patch.preview")
    await handler({"cwd": str(repo), "scope": "working"}, ctx)

    assert ctx.reply_error_payload is None
    payload = ctx.reply_payload
    assert payload["scope"] == "working"
    assert "diff --git" in payload["diff"]
    assert payload["stats"]["files"] == 1
    assert payload["stats"]["additions"] >= 2
    assert payload["stats"]["deletions"] >= 1
    assert payload["files"][0]["path"] == "app.py"
    assert payload["files"][0]["hunks"]


@pytest.mark.asyncio
async def test_patch_preview_rejects_unknown_scope(repo: Path) -> None:
    handler, ctx = _make_handler("patch.preview")
    await handler({"cwd": str(repo), "scope": "everything"}, ctx)

    assert ctx.reply_payload is None
    assert ctx.reply_error_payload is not None
    code, message, _ = ctx.reply_error_payload
    assert code == -32602
    assert "unknown scope" in message


@pytest.mark.asyncio
async def test_patch_preview_not_a_repo_returns_git_error(tmp_path: Path) -> None:
    empty = Path(_NOT_A_REPO_CEILING) / f"empty_{os.getpid()}_{id(tmp_path)}"
    empty.mkdir(exist_ok=True)
    handler, ctx = _make_handler("patch.preview")
    await handler({"cwd": str(empty), "scope": "working"}, ctx)

    assert ctx.reply_payload is None
    assert ctx.reply_error_payload is not None
    code, message, _ = ctx.reply_error_payload
    assert code == GIT_ERROR
    assert message
