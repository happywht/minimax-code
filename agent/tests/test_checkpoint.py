"""Tests for R310 workspace checkpoint layer.

Coverage:

* :class:`TestCheckpointDAO` — CRUD round-trip, list ordering/paging,
  delete semantics, JSON-column hydration against the real
  ``AsyncDatabase`` + migration 015.
* :class:`TestCheckpointManager` — the git-stash + untracked-copy runtime:
  clean vs dirty repos, restore (apply + skip-existing), diff preview,
  snapshot deletion, and fault tolerance on a non-git cwd.
* :class:`TestCheckpointIPC` — drives the five ``checkpoint.*`` handlers
  in-process via :class:`IPCServer` with injected DAO + manager.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from minimax_code.app import set_projects_dao, set_sessions_dao
from minimax_code.config import Config
from minimax_code.ipc.handlers_checkpoint import register_checkpoint_handlers
from minimax_code.ipc.protocol import INVALID_PARAMS
from minimax_code.ipc.server import IPCServer
from minimax_code.storage.dao.checkpoints import CheckpointDAO
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path
from minimax_code.workspace import CheckpointManager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return make_temp_database_path(tmp_path)


@pytest.fixture
async def async_db(db_path: Path) -> AsyncDatabase:
    db = AsyncDatabase(db_path)
    await db.connect()
    await db.migrate()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
def ckpt_dao(async_db: AsyncDatabase) -> CheckpointDAO:
    return CheckpointDAO(async_db)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A throwaway git repo with one committed file and a usable HEAD."""
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
def snapshot_root(tmp_path: Path) -> Path:
    root = tmp_path / "checkpoints"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def manager(snapshot_root: Path) -> CheckpointManager:
    return CheckpointManager(snapshot_root)


# ---------------------------------------------------------------------------
# DAO
# ---------------------------------------------------------------------------


class TestCheckpointDAO:
    @pytest.mark.asyncio
    async def test_create_and_get(self, ckpt_dao: CheckpointDAO) -> None:
        row = await ckpt_dao.create(
            session_id="sess_1",
            label="My checkpoint",
            message="before refactor",
            git_stash_ref="abc123",
            branch="main",
            tracked_files=["a.py", "b.py"],
            untracked_files=["c.txt"],
            has_untracked_snapshot=True,
        )
        assert row["id"].startswith("ckpt_")
        assert row["session_id"] == "sess_1"
        assert row["label"] == "My checkpoint"
        assert row["message"] == "before refactor"
        assert row["git_stash_ref"] == "abc123"
        assert row["branch"] == "main"
        assert row["tracked_files"] == ["a.py", "b.py"]
        assert row["untracked_files"] == ["c.txt"]
        assert row["has_untracked_snapshot"] is True
        assert row["created_at"]

        got = await ckpt_dao.get(row["id"])
        assert got is not None
        assert got["id"] == row["id"]
        # JSON columns round-trip back to lists.
        assert got["tracked_files"] == ["a.py", "b.py"]
        assert got["untracked_files"] == ["c.txt"]

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, ckpt_dao: CheckpointDAO) -> None:
        assert await ckpt_dao.get("ckpt_no_such") is None

    @pytest.mark.asyncio
    async def test_list_by_session_ordering_and_paging(
        self, ckpt_dao: CheckpointDAO
    ) -> None:
        # Inject distinct second-precision timestamps so ORDER BY
        # created_at DESC is deterministic (sub-second inserts would
        # otherwise share a timestamp and tie-break unpredictably).
        ids: list[str] = []
        for i in range(5):
            row = await ckpt_dao.create(
                session_id="sess_x",
                label=f"ckpt{i}",
                created_at=f"2026-07-25T10:00:0{i}Z",
            )
            ids.append(row["id"])
        # Another session's row must not leak in.
        await ckpt_dao.create(session_id="sess_other", label="other")

        rows = await ckpt_dao.list_by_session(session_id="sess_x", limit=50, offset=0)
        # newest first (DESC by created_at)
        assert [r["id"] for r in rows] == list(reversed(ids))

        # Paging windows are consistent with that ordering.
        page1 = await ckpt_dao.list_by_session(session_id="sess_x", limit=2, offset=0)
        page2 = await ckpt_dao.list_by_session(session_id="sess_x", limit=2, offset=2)
        assert [r["id"] for r in page1] == [ids[4], ids[3]]
        assert [r["id"] for r in page2] == [ids[2], ids[1]]

    @pytest.mark.asyncio
    async def test_delete(self, ckpt_dao: CheckpointDAO) -> None:
        row = await ckpt_dao.create(session_id="sess_d", label="del")
        assert await ckpt_dao.delete(row["id"]) is True
        assert await ckpt_dao.get(row["id"]) is None
        # Second delete on the same id reports nothing removed.
        assert await ckpt_dao.delete(row["id"]) is False

    @pytest.mark.asyncio
    async def test_delete_by_session(self, ckpt_dao: CheckpointDAO) -> None:
        await ckpt_dao.create(session_id="sess_bulk", label="a")
        await ckpt_dao.create(session_id="sess_bulk", label="b")
        await ckpt_dao.create(session_id="sess_keep", label="c")
        count = await ckpt_dao.delete_by_session("sess_bulk")
        assert count == 2
        assert await ckpt_dao.list_by_session(session_id="sess_bulk") == []
        kept = await ckpt_dao.list_by_session(session_id="sess_keep")
        assert len(kept) == 1

    @pytest.mark.asyncio
    async def test_hydrate_defaults(self, ckpt_dao: CheckpointDAO) -> None:
        row = await ckpt_dao.create(session_id="sess_h", label="minimal")
        assert row["tracked_files"] == []
        assert row["untracked_files"] == []
        assert row["has_untracked_snapshot"] is False
        assert row["git_stash_ref"] is None
        assert row["branch"] is None
        assert row["message"] == ""


# ---------------------------------------------------------------------------
# CheckpointManager
# ---------------------------------------------------------------------------


class TestCheckpointManager:
    @pytest.mark.asyncio
    async def test_create_clean_repo(self, manager: CheckpointManager, git_repo: Path) -> None:
        ckpt = await manager.create(
            cwd=git_repo,
            checkpoint_id="ckpt_clean",
            session_id="sess",
            label="clean",
        )
        assert ckpt.id == "ckpt_clean"
        assert ckpt.session_id == "sess"
        # Branch name depends on git's default; just assert it resolved.
        assert ckpt.branch is not None
        # Clean tree → nothing to stash, no untracked files.
        assert ckpt.git_stash_ref is None
        assert ckpt.tracked_files == []
        assert ckpt.untracked_files == []
        assert ckpt.has_untracked_snapshot is False
        assert ckpt.created_at

    @pytest.mark.asyncio
    async def test_create_dirty_repo_tracked(
        self, manager: CheckpointManager, git_repo: Path
    ) -> None:
        # Modify a tracked file (working-tree change) + stage a new file.
        (git_repo / "README.md").write_text("changed\n", encoding="utf-8")
        (git_repo / "new.py").write_text("print('hi')\n", encoding="utf-8")
        subprocess.run(["git", "add", "new.py"], cwd=git_repo, check=True)

        ckpt = await manager.create(
            cwd=git_repo,
            checkpoint_id="ckpt_dirty",
            session_id="sess",
            label="dirty",
        )
        # Both tracked changes land in the stash ref.
        assert ckpt.git_stash_ref is not None
        assert "README.md" in ckpt.tracked_files
        assert "new.py" in ckpt.tracked_files
        # Tracked files never trigger an untracked snapshot.
        assert ckpt.has_untracked_snapshot is False

    @pytest.mark.asyncio
    async def test_create_with_untracked_files(
        self, manager: CheckpointManager, git_repo: Path
    ) -> None:
        # An untracked (not staged) file.
        (git_repo / "scratch.txt").write_text("scratch\n", encoding="utf-8")

        ckpt = await manager.create(
            cwd=git_repo,
            checkpoint_id="ckpt_untr",
            session_id="sess",
            label="untracked",
        )
        assert "scratch.txt" in ckpt.untracked_files
        assert ckpt.has_untracked_snapshot is True
        # The file is physically copied into the snapshot dir.
        copied = manager.snapshot_dir("ckpt_untr") / "scratch.txt"
        assert copied.exists()
        assert copied.read_text(encoding="utf-8") == "scratch\n"

    @pytest.mark.asyncio
    async def test_restore_untracked_skips_existing(
        self, manager: CheckpointManager, git_repo: Path
    ) -> None:
        (git_repo / "note.txt").write_text("v1\n", encoding="utf-8")
        ckpt = await manager.create(
            cwd=git_repo,
            checkpoint_id="ckpt_rest",
            session_id="sess",
            label="rest",
        )
        assert ckpt.has_untracked_snapshot is True

        # Delete the file, then restore brings it back.
        (git_repo / "note.txt").unlink()
        result = await manager.restore(git_repo, ckpt)
        assert "note.txt" in result.restored_untracked
        assert (git_repo / "note.txt").read_text(encoding="utf-8") == "v1\n"

        # Restore again — destination now exists, must be skipped (never clobbered).
        result2 = await manager.restore(git_repo, ckpt)
        assert "note.txt" in result2.skipped_existing
        assert "note.txt" not in result2.restored_untracked

    @pytest.mark.asyncio
    async def test_restore_applies_stash(
        self, manager: CheckpointManager, git_repo: Path
    ) -> None:
        (git_repo / "README.md").write_text("edited\n", encoding="utf-8")
        ckpt = await manager.create(
            cwd=git_repo,
            checkpoint_id="ckpt_stash",
            session_id="sess",
            label="stash",
        )
        assert ckpt.git_stash_ref is not None

        # Revert the working tree, then restore must re-apply the stash.
        subprocess.run(["git", "checkout", "--", "README.md"], cwd=git_repo, check=True)
        assert (git_repo / "README.md").read_text(encoding="utf-8") == "hello\n"

        result = await manager.restore(git_repo, ckpt)
        assert result.applied_stash is True
        assert (git_repo / "README.md").read_text(encoding="utf-8") == "edited\n"

    @pytest.mark.asyncio
    async def test_diff_without_stash(
        self, manager: CheckpointManager, git_repo: Path
    ) -> None:
        ckpt = await manager.create(
            cwd=git_repo,
            checkpoint_id="ckpt_nodiff",
            session_id="sess",
            label="clean",
        )
        diff = await manager.diff(git_repo, ckpt)
        assert diff.available is False
        assert diff.patch == ""
        assert diff.files == []

    @pytest.mark.asyncio
    async def test_diff_with_stash(
        self, manager: CheckpointManager, git_repo: Path
    ) -> None:
        (git_repo / "README.md").write_text("edited\n", encoding="utf-8")
        ckpt = await manager.create(
            cwd=git_repo,
            checkpoint_id="ckpt_diff",
            session_id="sess",
            label="dirty",
        )
        diff = await manager.diff(git_repo, ckpt)
        assert diff.available is True
        assert diff.patch  # non-empty patch
        assert "README.md" in diff.files

    @pytest.mark.asyncio
    async def test_delete_snapshot_after_create(
        self, manager: CheckpointManager, git_repo: Path
    ) -> None:
        (git_repo / "u.txt").write_text("u\n", encoding="utf-8")
        await manager.create(
            cwd=git_repo,
            checkpoint_id="ckpt_del",
            session_id="sess",
            label="del",
        )
        assert manager.snapshot_dir("ckpt_del").exists()
        assert manager.delete_snapshot("ckpt_del") is True
        assert not manager.snapshot_dir("ckpt_del").exists()
        # Idempotent — safe to call for a checkpoint that had no snapshot.
        assert manager.delete_snapshot("ckpt_del") is False

    @pytest.mark.asyncio
    async def test_create_fault_tolerant_non_git(
        self, manager: CheckpointManager, tmp_path: Path
    ) -> None:
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        # No `git init`. The manager must NOT raise — it records warnings
        # and returns an empty-but-valid checkpoint (the same "partial
        # trajectory still lands" contract as the self-evolution runner).
        ckpt = await manager.create(
            cwd=non_repo,
            checkpoint_id="ckpt_nogit",
            session_id="sess",
            label="nogit",
        )
        assert ckpt.id == "ckpt_nogit"
        assert ckpt.git_stash_ref is None
        assert ckpt.branch is None
        assert ckpt.tracked_files == []
        assert ckpt.untracked_files == []
        assert ckpt.has_untracked_snapshot is False


# ---------------------------------------------------------------------------
# IPC handlers
# ---------------------------------------------------------------------------


class TestCheckpointIPC:
    @pytest.fixture
    def server(self, ckpt_dao: CheckpointDAO, manager: CheckpointManager) -> IPCServer:
        cfg = Config.from_env()
        srv = IPCServer(config=cfg, stdin=sys.stdin, stdout=sys.stdout)
        # Inject the DAO + manager so handlers skip the lazy factories.
        register_checkpoint_handlers(srv, dao=ckpt_dao, manager=manager)
        return srv

    async def _call(self, server: IPCServer, method: str, params: dict[str, Any]) -> dict:
        """Dispatch via the real handle_request path; return the full response."""
        resp = await server.handle_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        })
        assert resp is not None
        return resp

    @pytest.mark.asyncio
    async def test_create_requires_session_id(self, server: IPCServer) -> None:
        resp = await self._call(server, "checkpoint.create", {})
        assert "error" in resp
        assert resp["error"]["code"] == INVALID_PARAMS

    @pytest.mark.asyncio
    async def test_create_and_list(
        self, server: IPCServer, git_repo: Path
    ) -> None:
        created = await self._call(server, "checkpoint.create", {
            "session_id": "sess_ipc",
            "label": "ipc ckpt",
            "message": "via rpc",
            "cwd": str(git_repo),
        })
        assert "result" in created
        row = created["result"]["checkpoint"]
        assert row["session_id"] == "sess_ipc"
        assert row["label"] == "ipc ckpt"
        assert row["message"] == "via rpc"
        assert row["id"].startswith("ckpt_")

        listed = await self._call(server, "checkpoint.list", {"session_id": "sess_ipc"})
        assert listed["result"]["count"] == 1
        assert listed["result"]["checkpoints"][0]["id"] == row["id"]

    @pytest.mark.asyncio
    async def test_list_clamps_limit(
        self, server: IPCServer, git_repo: Path
    ) -> None:
        # A runaway limit is clamped to _MAX_LIMIT (200); we only create 3.
        for i in range(3):
            await self._call(server, "checkpoint.create", {
                "session_id": "sess_limit",
                "cwd": str(git_repo),
                "label": f"c{i}",
            })
        resp = await self._call(server, "checkpoint.list", {
            "session_id": "sess_limit",
            "limit": 99999,
        })
        assert resp["result"]["count"] == 3

    @pytest.mark.asyncio
    async def test_restore_unknown_checkpoint(
        self, server: IPCServer, git_repo: Path
    ) -> None:
        resp = await self._call(server, "checkpoint.restore", {
            "checkpoint_id": "ckpt_no_such",
            "cwd": str(git_repo),
        })
        assert "error" in resp
        assert resp["error"]["code"] == INVALID_PARAMS

    @pytest.mark.asyncio
    async def test_restore_known_checkpoint(
        self, server: IPCServer, git_repo: Path
    ) -> None:
        created = await self._call(server, "checkpoint.create", {
            "session_id": "sess_r",
            "cwd": str(git_repo),
        })
        ckpt_id = created["result"]["checkpoint"]["id"]
        resp = await self._call(server, "checkpoint.restore", {
            "checkpoint_id": ckpt_id,
            "cwd": str(git_repo),
        })
        assert "result" in resp
        result = resp["result"]["result"]
        assert result["checkpoint_id"] == ckpt_id
        # The restore result shape is fully populated.
        assert "restored" in result
        assert "applied_stash" in result
        assert "restored_untracked" in result
        assert "skipped_existing" in result
        assert "warnings" in result

    @pytest.mark.asyncio
    async def test_diff_handler(
        self, server: IPCServer, git_repo: Path
    ) -> None:
        created = await self._call(server, "checkpoint.create", {
            "session_id": "sess_d",
            "cwd": str(git_repo),
        })
        ckpt_id = created["result"]["checkpoint"]["id"]
        resp = await self._call(server, "checkpoint.diff", {
            "checkpoint_id": ckpt_id,
            "cwd": str(git_repo),
        })
        assert "result" in resp
        assert resp["result"]["checkpoint_id"] == ckpt_id
        # Clean repo → no stash → diff not available.
        assert resp["result"]["available"] is False

    @pytest.mark.asyncio
    async def test_delete_unknown_checkpoint(self, server: IPCServer) -> None:
        resp = await self._call(server, "checkpoint.delete", {"checkpoint_id": "ckpt_x"})
        assert "error" in resp
        assert resp["error"]["code"] == INVALID_PARAMS

    @pytest.mark.asyncio
    async def test_delete_known_checkpoint(
        self, server: IPCServer, git_repo: Path
    ) -> None:
        created = await self._call(server, "checkpoint.create", {
            "session_id": "sess_del",
            "cwd": str(git_repo),
        })
        ckpt_id = created["result"]["checkpoint"]["id"]
        resp = await self._call(server, "checkpoint.delete", {"checkpoint_id": ckpt_id})
        assert "result" in resp
        assert resp["result"]["ok"] is True
        assert "removed_snapshot" in resp["result"]

        # A second delete now 404s — the row is gone.
        again = await self._call(server, "checkpoint.delete", {"checkpoint_id": ckpt_id})
        assert "error" in again


# ---------------------------------------------------------------------------
# v1.3.0 — project-scoped cwd resolution
# ---------------------------------------------------------------------------


class _SpyManager:
    """Records the cwd each handler call resolved to; returns minimal fakes."""

    def __init__(self) -> None:
        self.cwds: list[str] = []

    async def create(self, *, cwd: Path, **kwargs: Any) -> Any:
        self.cwds.append(str(cwd))
        return SimpleNamespace(
            git_stash_ref=None,
            branch="main",
            tracked_files=[],
            untracked_files=[],
            has_untracked_snapshot=False,
            created_at="2026-01-01T00:00:00Z",
        )

    async def restore(self, cwd: Path, checkpoint: Any) -> Any:
        self.cwds.append(str(cwd))
        return SimpleNamespace(
            checkpoint_id=checkpoint.id,
            restored=True,
            applied_stash=None,
            restored_untracked=False,
            skipped_existing=[],
            warnings=[],
        )

    async def diff(self, cwd: Path, checkpoint: Any) -> Any:
        self.cwds.append(str(cwd))
        return SimpleNamespace(
            checkpoint_id=checkpoint.id, available=False, patch="", files=[]
        )

    def delete_snapshot(self, checkpoint_id: str) -> bool:
        return False


class TestCheckpointProjectRoot:
    """``checkpoint.*`` resolves cwd from the session's project root.

    create/restore/diff resolve the workdir via ``resolve_root_for_session``
    when no explicit ``cwd`` is given; an explicit ``cwd`` still wins
    (legacy semantics), and a session without a project root keeps the
    git-toplevel fallback untouched.
    """

    @pytest.fixture
    async def rooted_session(
        self, tmp_path: Path, async_db: AsyncDatabase, git_repo: Path
    ):
        projects = ProjectsDAO(async_db)
        await projects.ensure_inbox()
        sessions = SessionsDAO(async_db)
        pid = "proj_ck"
        await projects.create(id=pid, name="CK", root_path=str(git_repo))
        sid = "ses_ck"
        await sessions.create(id=sid, title="ck", project_id=pid)
        set_projects_dao(projects)
        set_sessions_dao(sessions)
        try:
            yield sid
        finally:
            set_projects_dao(None)
            set_sessions_dao(None)

    def _server(self, ckpt_dao: CheckpointDAO, spy: _SpyManager) -> IPCServer:
        srv = IPCServer(config=Config.from_env(), stdin=sys.stdin, stdout=sys.stdout)
        register_checkpoint_handlers(srv, dao=ckpt_dao, manager=spy)
        return srv

    async def _call(self, server: IPCServer, method: str, params: dict) -> dict:
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
        })
        assert resp is not None
        return resp

    @pytest.mark.asyncio
    async def test_create_anchors_at_session_project_root(
        self, rooted_session, ckpt_dao: CheckpointDAO, git_repo: Path
    ) -> None:
        spy = _SpyManager()
        server = self._server(ckpt_dao, spy)
        resp = await self._call(
            server, "checkpoint.create", {"session_id": rooted_session}
        )
        assert "result" in resp, resp
        assert spy.cwds == [str(git_repo.resolve())]

    @pytest.mark.asyncio
    async def test_explicit_cwd_wins_over_session_root(
        self, rooted_session, ckpt_dao: CheckpointDAO, tmp_path: Path
    ) -> None:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        spy = _SpyManager()
        server = self._server(ckpt_dao, spy)
        resp = await self._call(server, "checkpoint.create", {
            "session_id": rooted_session,
            "cwd": str(elsewhere),
        })
        assert "result" in resp, resp
        assert spy.cwds == [str(elsewhere.resolve())]

    @pytest.mark.asyncio
    async def test_restore_uses_checkpoint_owning_session_root(
        self, rooted_session, ckpt_dao: CheckpointDAO, git_repo: Path
    ) -> None:
        # Seed a row owned by the rooted session; restore must resolve
        # the workdir from that session's project, not the process cwd.
        row = await ckpt_dao.create(
            session_id=rooted_session,
            label="seed",
            message="",
            git_stash_ref=None,
            branch="main",
            tracked_files=[],
            untracked_files=[],
            has_untracked_snapshot=False,
            checkpoint_id="ckpt_seed",
            created_at="2026-01-01T00:00:00Z",
        )
        spy = _SpyManager()
        server = self._server(ckpt_dao, spy)
        resp = await self._call(
            server, "checkpoint.restore", {"checkpoint_id": row["id"]}
        )
        assert "result" in resp, resp
        assert spy.cwds == [str(git_repo.resolve())]

    @pytest.mark.asyncio
    async def test_session_without_project_keeps_git_toplevel(
        self, ckpt_dao: CheckpointDAO, async_db: AsyncDatabase, git_repo: Path
    ) -> None:
        sessions = SessionsDAO(async_db)
        await sessions.create(id="ses_plain", title="plain")
        set_sessions_dao(sessions)
        try:
            spy = _SpyManager()
            server = self._server(ckpt_dao, spy)
            resp = await self._call(
                server, "checkpoint.create", {"session_id": "ses_plain"}
            )
            assert "result" in resp, resp
            # Fallback = the process git toplevel (the agent repo), NOT
            # the fixture repo — proves the legacy path is untouched.
            cwd = Path(spy.cwds[0])
            assert cwd != git_repo.resolve()
            assert (cwd / ".git").exists()
        finally:
            set_sessions_dao(None)
