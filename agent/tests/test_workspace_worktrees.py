"""Tests for workspace/worktree IPC handlers."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from minimax_code.config import Config
from minimax_code.ipc.handlers_workspace import register_workspace_handlers
from minimax_code.ipc.server import IPCServer
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


@pytest.fixture
async def sessions_dao(tmp_path: Path) -> SessionsDAO:
    db = AsyncDatabase(make_temp_database_path(tmp_path))
    await db.connect()
    await db.migrate()
    try:
        yield SessionsDAO(db)
    finally:
        await db.close()


async def request(server: IPCServer, stdout: io.StringIO, method: str, params: dict) -> dict:
    req_id = f"test-{method}"
    before = stdout.tell()
    await server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}) + "\n"
    )
    tail = stdout.getvalue()[before:]
    for line in tail.splitlines():
        payload = json.loads(line)
        if payload.get("id") == req_id:
            if "error" in payload:
                raise RuntimeError(payload["error"])
            return payload["result"]
    raise AssertionError(f"no response for {method}")


@pytest.mark.asyncio
async def test_create_worktree_session_persists_metadata(
    tmp_path: Path,
    git_repo: Path,
    sessions_dao: SessionsDAO,
) -> None:
    stdout = io.StringIO()
    server = IPCServer(config=Config.from_env(), stdin=io.StringIO(), stdout=stdout)
    worktree_root = tmp_path / "managed-worktrees"
    register_workspace_handlers(
        server,
        dao=sessions_dao,
        repo_root=git_repo,
        worktree_root=worktree_root,
    )

    result = await request(
        server,
        stdout,
        "workspace.create_worktree_session",
        {"title": "isolated task", "base_ref": "HEAD"},
    )

    session = result["session"]
    assert session["workspace_mode"] == "worktree"
    assert session["title"] == "isolated task"
    assert Path(session["workspace_path"]).exists()
    assert worktree_root in Path(session["workspace_path"]).resolve().parents
    assert (Path(session["workspace_path"]) / "README.md").exists()

    listed = await request(server, stdout, "workspace.list_worktrees", {})
    assert [s["id"] for s in listed["sessions"]] == [session["id"]]


@pytest.mark.asyncio
async def test_delete_worktree_session_returns_to_local(
    tmp_path: Path,
    git_repo: Path,
    sessions_dao: SessionsDAO,
) -> None:
    stdout = io.StringIO()
    server = IPCServer(config=Config.from_env(), stdin=io.StringIO(), stdout=stdout)
    worktree_root = tmp_path / "managed-worktrees"
    register_workspace_handlers(
        server,
        dao=sessions_dao,
        repo_root=git_repo,
        worktree_root=worktree_root,
    )
    created = await request(
        server,
        stdout,
        "workspace.create_worktree_session",
        {"title": "isolated task", "base_ref": "HEAD"},
    )
    path = Path(created["session"]["workspace_path"])

    deleted = await request(
        server,
        stdout,
        "workspace.delete_worktree",
        {"session_id": created["session_id"]},
    )

    assert deleted["ok"] is True
    assert deleted["session"]["workspace_mode"] == "local"
    assert not path.exists()
