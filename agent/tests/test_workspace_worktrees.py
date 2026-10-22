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


class _StubProjectsDAO:
    """Minimal ProjectsDAO.get stand-in for the v1.3.0 project_id gate."""

    def __init__(self, known: set[str], root: Path | None = None) -> None:
        self._known = known
        self._root = root

    async def get(self, project_id: str) -> object | None:
        return {"root_path": str(self._root) if self._root else ""} if project_id in self._known else None


@pytest.mark.asyncio
async def test_create_worktree_session_files_under_project_id(
    tmp_path: Path,
    git_repo: Path,
    sessions_dao: SessionsDAO,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v1.3.0: a rooted project_id lands on the session; no id → inbox."""
    import minimax_code.app as app_mod

    monkeypatch.setattr(
        app_mod, "get_projects_dao", lambda: _StubProjectsDAO(known={"p_rooted"})
    )

    stdout = io.StringIO()
    server = IPCServer(config=Config.from_env(), stdin=io.StringIO(), stdout=stdout)
    register_workspace_handlers(
        server,
        dao=sessions_dao,
        repo_root=git_repo,
        worktree_root=tmp_path / "managed-worktrees",
    )

    rooted = await request(
        server,
        stdout,
        "workspace.create_worktree_session",
        {"project_id": "p_rooted"},
    )
    assert rooted["session"]["project_id"] == "p_rooted"

    legacy = await request(server, stdout, "workspace.create_worktree_session", {})
    assert legacy["session"]["project_id"] == "inbox"


@pytest.mark.asyncio
async def test_create_worktree_session_rejects_unknown_project_id(
    tmp_path: Path,
    git_repo: Path,
    sessions_dao: SessionsDAO,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v1.3.0: a ghost project_id fails fast, before any checkout exists."""
    import minimax_code.app as app_mod

    monkeypatch.setattr(app_mod, "get_projects_dao", lambda: _StubProjectsDAO(known=set()))

    stdout = io.StringIO()
    server = IPCServer(config=Config.from_env(), stdin=io.StringIO(), stdout=stdout)
    worktree_root = tmp_path / "managed-worktrees"
    register_workspace_handlers(
        server,
        dao=sessions_dao,
        repo_root=git_repo,
        worktree_root=worktree_root,
    )

    with pytest.raises(RuntimeError, match="unknown project_id"):
        await request(
            server,
            stdout,
            "workspace.create_worktree_session",
            {"project_id": "ghost"},
        )

    # Fail-fast guarantee: no orphaned checkout was created.
    if worktree_root.exists():
        assert list(worktree_root.iterdir()) == []


async def test_bound_project_overrides_process_repository(tmp_path, git_repo, sessions_dao, monkeypatch):
    import minimax_code.app as app_mod

    monkeypatch.setattr(app_mod, "get_projects_dao",
                        lambda: _StubProjectsDAO({"selected"}, git_repo))
    wrong = tmp_path / "wrong-default"
    wrong.mkdir()
    stdout = io.StringIO()
    server = IPCServer(config=Config.from_env(), stdin=io.StringIO(), stdout=stdout)
    register_workspace_handlers(server, dao=sessions_dao, repo_root=wrong,
                                worktree_root=tmp_path / "worktrees")
    result = await request(server, stdout, "workspace.create_worktree_session",
                           {"project_id": "selected"})
    assert (Path(result["worktree_path"]) / "README.md").read_text() == "hello\n"


async def test_missing_bound_project_does_not_fall_back(tmp_path, git_repo, sessions_dao, monkeypatch):
    import minimax_code.app as app_mod

    monkeypatch.setattr(app_mod, "get_projects_dao",
                        lambda: _StubProjectsDAO({"selected"}, tmp_path / "gone"))
    stdout = io.StringIO()
    server = IPCServer(config=Config.from_env(), stdin=io.StringIO(), stdout=stdout)
    root = tmp_path / "worktrees"
    register_workspace_handlers(server, dao=sessions_dao, repo_root=git_repo, worktree_root=root)
    with pytest.raises(RuntimeError, match="unavailable"):
        await request(server, stdout, "workspace.create_worktree_session", {"project_id": "selected"})
    assert not root.exists()


@pytest.mark.parametrize("kind", ["dirty", "unregistered", "managed-root"])
async def test_delete_preserves_user_files_on_refusal(tmp_path, git_repo, sessions_dao, kind):
    stdout = io.StringIO()
    server = IPCServer(config=Config.from_env(), stdin=io.StringIO(), stdout=stdout)
    root = tmp_path / "worktrees"
    register_workspace_handlers(server, dao=sessions_dao, repo_root=git_repo, worktree_root=root)
    created = await request(server, stdout, "workspace.create_worktree_session", {})
    original = Path(created["worktree_path"])
    target = original if kind == "dirty" else (root if kind == "managed-root" else root / "unregistered")
    target.mkdir(exist_ok=True)
    marker = target / "user-data.txt"
    marker.write_text("keep me", encoding="utf-8")
    await sessions_dao.update(created["session_id"], workspace_path=str(target))
    with pytest.raises(RuntimeError):
        await request(server, stdout, "workspace.delete_worktree", {"session_id": created["session_id"]})
    assert marker.read_text(encoding="utf-8") == "keep me"
    assert original.exists()
    assert (await sessions_dao.get(created["session_id"]))["workspace_mode"] == "worktree"


@pytest.mark.parametrize("reference", ["--orphan", "--force", "HEAD\0ignored", "missing-ref", "HEAD:README.md"])
async def test_create_rejects_options_missing_refs_and_non_commits(tmp_path, git_repo, sessions_dao, reference):
    stdout = io.StringIO()
    server = IPCServer(config=Config.from_env(), stdin=io.StringIO(), stdout=stdout)
    root = tmp_path / "worktrees"
    register_workspace_handlers(server, dao=sessions_dao, repo_root=git_repo, worktree_root=root)
    with pytest.raises(RuntimeError):
        await request(server, stdout, "workspace.create_worktree_session", {"base_ref": reference})
    assert not root.exists()
    assert await sessions_dao.list() == []


async def test_create_resolves_annotated_tag_to_commit(tmp_path, git_repo, sessions_dao):
    subprocess.run(["git", "tag", "-a", "release", "-m", "release"], cwd=git_repo, check=True)
    stdout = io.StringIO()
    server = IPCServer(config=Config.from_env(), stdin=io.StringIO(), stdout=stdout)
    register_workspace_handlers(server, dao=sessions_dao, repo_root=git_repo,
                                worktree_root=tmp_path / "worktrees")
    result = await request(server, stdout, "workspace.create_worktree_session", {"base_ref": "release"})
    assert result["base_branch"] == "release"
    assert (Path(result["worktree_path"]) / "README.md").read_text() == "hello\n"
