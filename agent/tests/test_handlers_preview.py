"""Tests for the ``preview.*`` IPC handlers (v1.7.1).

``preview.set_root`` is exercised end-to-end through the registered
handler with a real :class:`PreviewState` and a fake ProjectsDAO injected
via the ``state`` / ``dao`` test seams — same ``_CapturedReply`` harness
as ``test_handlers_telemetry.py``.

We assert:

* A project with a bound ``root_path`` re-roots the state and the reply
  echoes the resolved workspace.
* Omitted ``project_id`` (and a project *without* a bound root) degrade
  to the process-default root (``MINIMAX_CODE_WORKSPACE``).
* Unknown ``project_id`` and a ``root_path`` pointing at a missing
  directory both fail fast with ``-32602`` and leave the state untouched.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from minimax_code.config import Config
from minimax_code.ipc.handlers_preview import register_preview_handlers
from minimax_code.ipc.server import IPCServer
from minimax_code.preview.server import PreviewState


class _CapturedReply:
    def __init__(self) -> None:
        self.reply_value: dict[str, Any] | None = None
        self.error_value: dict[str, Any] | None = None

    async def reply(self, value: Any) -> None:
        self.reply_value = value

    async def reply_error(self, code: int, message: str, data: Any = None) -> None:
        self.error_value = {"code": code, "message": message, "data": data}

    async def emit(self, event: str, data: Any) -> None:  # pragma: no cover
        return None


class _FakeProjectsDAO:
    def __init__(self, projects: dict[str, dict[str, Any]]) -> None:
        self._projects = projects

    async def get(self, project_id: str) -> dict[str, Any] | None:
        return self._projects.get(project_id)


@pytest.fixture
def initial_root(tmp_path: Path) -> Path:
    root = tmp_path / "initial-root"
    root.mkdir()
    return root


@pytest.fixture
def preview_state(initial_root: Path) -> PreviewState:
    return PreviewState(initial_root)


@pytest.fixture
def handler(preview_state: PreviewState, tmp_path: Path):  # type: ignore[no-untyped-def]
    """Register preview handlers with a fake DAO over two projects."""
    proj_a = tmp_path / "proj-a"
    proj_a.mkdir()
    dao = _FakeProjectsDAO(
        {
            "pA": {"id": "pA", "name": "A", "root_path": str(proj_a)},
            "pB": {"id": "pB", "name": "B", "root_path": ""},  # unbound
            "pGhost": {"id": "pGhost", "name": "G", "root_path": str(tmp_path / "gone")},
        }
    )
    server = IPCServer(config=Config.from_env(), stdin=io.StringIO(), stdout=io.StringIO())
    register_preview_handlers(server, state=preview_state, dao=dao)
    registered = server._handlers.get("preview.set_root")
    assert registered is not None, "preview.set_root not registered"
    return registered


async def _call(handler: Any, params: dict[str, Any] | None) -> _CapturedReply:
    ctx = _CapturedReply()
    await handler(params or {}, ctx)
    return ctx


class TestSetRootHappyPath:
    @pytest.mark.asyncio
    async def test_project_with_root_re_roots_state(
        self, handler: Any, preview_state: PreviewState, tmp_path: Path
    ) -> None:
        ctx = await _call(handler, {"project_id": "pA"})
        assert ctx.error_value is None
        assert ctx.reply_value is not None
        assert ctx.reply_value["ok"] is True
        assert ctx.reply_value["project_id"] == "pA"
        assert ctx.reply_value["workspace"] == str((tmp_path / "proj-a").resolve())
        assert preview_state.workspace == (tmp_path / "proj-a").resolve()

    @pytest.mark.asyncio
    async def test_omitted_project_id_uses_process_default(
        self,
        handler: Any,
        preview_state: PreviewState,
        initial_root: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        env_root = tmp_path / "env-root"
        env_root.mkdir()
        monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(env_root))
        ctx = await _call(handler, None)
        assert ctx.error_value is None
        assert ctx.reply_value is not None
        assert ctx.reply_value["project_id"] is None
        assert ctx.reply_value["workspace"] == str(env_root.resolve())
        assert preview_state.workspace == env_root.resolve()

    @pytest.mark.asyncio
    async def test_project_without_root_degrades_to_default(
        self,
        handler: Any,
        preview_state: PreviewState,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        env_root = tmp_path / "env-root"
        env_root.mkdir()
        monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(env_root))
        ctx = await _call(handler, {"project_id": "pB"})
        assert ctx.error_value is None
        assert ctx.reply_value is not None
        assert ctx.reply_value["project_id"] == "pB"
        # Unbound project → process default, not the initial boot root.
        assert ctx.reply_value["workspace"] == str(env_root.resolve())

    @pytest.mark.asyncio
    async def test_blank_project_id_treated_as_omitted(
        self,
        handler: Any,
        preview_state: PreviewState,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        env_root = tmp_path / "env-root"
        env_root.mkdir()
        monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(env_root))
        ctx = await _call(handler, {"project_id": "   "})
        assert ctx.error_value is None
        assert ctx.reply_value is not None
        assert ctx.reply_value["project_id"] is None


class TestSetRootFastFail:
    @pytest.mark.asyncio
    async def test_unknown_project_id_fails_fast(
        self, handler: Any, preview_state: PreviewState, initial_root: Path
    ) -> None:
        ctx = await _call(handler, {"project_id": "nope"})
        assert ctx.reply_value is None
        assert ctx.error_value is not None
        assert ctx.error_value["code"] == -32602
        assert "unknown project_id" in ctx.error_value["message"]
        assert preview_state.workspace == initial_root.resolve()  # untouched

    @pytest.mark.asyncio
    async def test_missing_root_dir_fails_fast(
        self, handler: Any, preview_state: PreviewState, initial_root: Path
    ) -> None:
        ctx = await _call(handler, {"project_id": "pGhost"})
        assert ctx.reply_value is None
        assert ctx.error_value is not None
        assert ctx.error_value["code"] == -32602
        assert "not an existing directory" in ctx.error_value["message"]
        assert preview_state.workspace == initial_root.resolve()  # untouched
