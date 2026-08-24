"""v1.3.0 — ``patch.*`` handlers honour ``params.project_id`` scoping.

``_scoped_cwd`` (shared with ``git.*``) anchors the patch pipeline at
the project root: a missing ``cwd`` defaults to the root, an explicit
``cwd`` must resolve inside it, and params without a rooted
``project_id`` keep the legacy behaviour byte-for-byte.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest

from minimax_code.app import set_projects_dao
from minimax_code.config import Config
from minimax_code.ipc.handlers_patch import register_patch_handlers
from minimax_code.ipc.protocol import INVALID_PARAMS
from minimax_code.ipc.server import IPCServer
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path


def _git_available() -> bool:
    """True iff the host has a ``git`` binary on PATH."""
    return shutil.which("git") is not None


pytestmark = pytest.mark.skipif(
    not _git_available(), reason="git binary required for these tests"
)


def _build_repo(tmp_path: Path) -> Path:
    """Fresh git repo with two commits and an unstaged ``src/app.py`` edit."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "Test")
    env.setdefault("GIT_AUTHOR_EMAIL", "test@example.com")
    env.setdefault("GIT_COMMITTER_NAME", "Test")
    env.setdefault("GIT_COMMITTER_EMAIL", "test@example.com")
    env.setdefault("GIT_PAGER", "cat")
    # Cap upward search so we don't accidentally pick up an outer .git.
    env["GIT_CEILING_DIRECTORIES"] = str(tmp_path)

    def _run(*args: str) -> None:
        subprocess.check_call(["git", *args], cwd=str(repo), env=env)

    _run("init", "--initial-branch=main")
    _run("config", "user.email", "test@example.com")
    _run("config", "user.name", "Test")

    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _run("add", "README.md")
    _run("commit", "-m", "initial")

    src = repo / "src"
    src.mkdir()
    app = src / "app.py"
    app.write_text("def main():\n    return 1\n", encoding="utf-8")
    _run("add", "src/app.py")
    _run("commit", "-m", "add app")

    # Unstaged edit — visible to ``git diff`` (working scope).
    app.write_text(
        "def main():\n    return 1\n\ndef helper():\n    return 2\n",
        encoding="utf-8",
    )
    return repo


class _CapturingContext:
    """Drop-in :class:`Context` that records the reply envelope."""

    def __init__(self) -> None:
        self.reply_payload: Any = None
        self.reply_error_payload: tuple[int, str, Any] | None = None

    async def reply(self, result: Any) -> None:
        self.reply_payload = result

    async def reply_error(self, code: int, message: str, data: Any = None) -> None:
        self.reply_error_payload = (code, message, data)

    async def emit(self, event: str, data: Any = None) -> None:  # pragma: no cover
        return None


def _make_handler(
    method: str,
) -> tuple[Callable[..., Coroutine[Any, Any, None]], _CapturingContext]:
    server = IPCServer(config=Config.from_env(), stdin=sys.stdin, stdout=sys.stdout)
    register_patch_handlers(server)
    return server._handlers[method], _CapturingContext()  # type: ignore[return-value]


class TestPatchProjectScope:
    """``patch.*`` preview/apply gate their cwd on the project root."""

    @pytest.fixture
    async def rooted(self, tmp_path: Path):
        """Two temp repos; project ``proj_rooted`` is bound to repo_a."""
        db = AsyncDatabase(make_temp_database_path(tmp_path))
        await db.connect()
        await db.migrate()
        dao = ProjectsDAO(db)
        await dao.ensure_inbox()

        repo_a = _build_repo(tmp_path / "A")  # keeps the unstaged edit
        repo_b = _build_repo(tmp_path / "B")  # committed clean below
        subprocess.check_call(["git", "add", "-A"], cwd=str(repo_b))
        subprocess.check_call(["git", "commit", "-m", "clean"], cwd=str(repo_b))

        await dao.create(id="proj_rooted", name="Rooted", root_path=str(repo_a))
        await dao.create(id="proj_rootless", name="Rootless")
        set_projects_dao(dao)
        try:
            yield repo_a, repo_b
        finally:
            set_projects_dao(None)
            await db.close()

    @pytest.mark.asyncio
    async def test_preview_default_cwd_anchors_at_project_root(self, rooted) -> None:
        repo_a, _ = rooted
        handler, ctx = _make_handler("patch.preview")
        await handler({"project_id": "proj_rooted"}, ctx)
        assert ctx.reply_error_payload is None
        # repo_a's unstaged edit is the working diff — proves the call
        # ran inside repo_a, not the process cwd.
        assert "+def helper():" in ctx.reply_payload["diff"]

    @pytest.mark.asyncio
    async def test_preview_explicit_cwd_outside_root_rejected(self, rooted) -> None:
        repo_a, repo_b = rooted
        handler, ctx = _make_handler("patch.preview")
        await handler({"project_id": "proj_rooted", "cwd": str(repo_b)}, ctx)
        assert ctx.reply_payload is None
        code, message, _ = ctx.reply_error_payload
        assert code == INVALID_PARAMS
        assert "outside the project root" in message

    @pytest.mark.asyncio
    async def test_apply_file_outside_root_rejected_before_exec(self, rooted) -> None:
        repo_a, repo_b = rooted
        handler, ctx = _make_handler("patch.apply_file")
        # The containment gate fires during cwd resolution — before any
        # ``git apply`` runs — so nothing touches repo_b.
        await handler(
            {
                "project_id": "proj_rooted",
                "cwd": str(repo_b),
                "file_path": "src/app.py",
            },
            ctx,
        )
        assert ctx.reply_payload is None
        code, message, _ = ctx.reply_error_payload
        assert code == INVALID_PARAMS
        assert "outside the project root" in message

    @pytest.mark.asyncio
    async def test_no_project_id_keeps_legacy_behaviour(self, rooted) -> None:
        _, repo_b = rooted
        handler, ctx = _make_handler("patch.preview")
        # No project_id → cwd passes through untouched (legacy).
        await handler({"cwd": str(repo_b)}, ctx)
        assert ctx.reply_error_payload is None
        assert ctx.reply_payload["diff"] == ""  # repo_b is clean
