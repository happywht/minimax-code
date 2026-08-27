"""Regression tests for model→provider routing in ``model.set_current``.

Field report (v1.6.2): switching to a third-party model (e.g.
``glm-5.3``) from the UI kept routing every request to the MiniMax
provider — burning the MiniMax quota while the third-party provider's
own quota sat untouched. Root cause chain:

1. The frontend ``setCurrentModel`` sends only ``model_id``;
2. ``ModelPrefsDAO.set_current`` pins a missing ``provider_id`` to
   ``"builtin-minimax"``;
3. The rebuilt LLM client then combined third-party model name +
   MiniMax base_url + the MiniMax fallback key.

The handler now resolves the owning provider from the enabled
provider list (each ``list_models`` entry carries ``provider_id``)
whenever the caller omits ``provider_id``. These tests pin that
resolution: third-party models route to their provider, builtin
models keep routing to ``builtin-minimax``, an explicit
``provider_id`` still wins, and duplicate model ids resolve to the
first provider by name order (mirroring ``ProviderDAO.list``'s
``ORDER BY name``).

Field report (v1.6.3, the read-side sequel): the v1.6.2 fix repaired
the *write* side (``set_current`` persists ``provider_id`` and the
rebuilt singleton carries the right protocol/base_url/key/model), but
``agent.send_message`` built its ``AgentConfig`` without a ``model``
argument — so the run loop stamped the dataclass default
``"MiniMax-M3"`` on every stream call regardless of the singleton.
Users saw ``400 {"code": "1214", "message": "modelCode：不存在"}`` from
whichever third-party provider was active, no matter which model they
selected. The tests at the bottom pin that ``AgentConfig.model`` now
mirrors the singleton's ``default_model``.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from minimax_code.config import Config
from minimax_code.ipc.builtins import handle_agent_send_message
from minimax_code.ipc.client import IPCClient
from minimax_code.ipc.server import IPCServer
from minimax_code.models import default_model
from minimax_code.storage.dao.model_prefs import ModelPrefsDAO
from minimax_code.storage.dao.providers import ProviderDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path


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


def _make_client(db: AsyncDatabase) -> IPCClient:
    """IPCClient whose model handlers share the test DB (mirrors test_model)."""
    client = IPCClient()
    client.server._model_prefs_dao = ModelPrefsDAO(db)
    client.server._model_prefs_dao_lock = asyncio.Lock()
    client.server._provider_dao_for_model = ProviderDAO(db)
    client.server._provider_dao_for_model_lock = asyncio.Lock()
    return client


async def _add_zhipu(db: AsyncDatabase) -> dict:
    """Insert the third-party provider from the field report."""
    prov_dao = ProviderDAO(db)
    return await prov_dao.create(
        name="Zhipu",
        protocol="openai",
        base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        models=[{"id": "glm-5.3", "name": "GLM-5.3"}],
    )


# ---------------------------------------------------------------------------
# The bug
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_third_party_model_routes_to_its_provider(async_db: AsyncDatabase) -> None:
    """glm-5.3 without provider_id must land on the Zhipu provider,
    not the hardcoded ``builtin-minimax`` fallback."""
    glm = await _add_zhipu(async_db)

    client = _make_client(async_db)
    result = await client.request("model.set_current", {"model": "glm-5.3"})
    assert result["ok"] is True

    pref = await ModelPrefsDAO(async_db).get_current()
    assert pref["model_id"] == "glm-5.3"
    assert pref["provider_id"] == glm["id"]


@pytest.mark.asyncio
async def test_builtin_model_still_routes_to_builtin_minimax(
    async_db: AsyncDatabase,
) -> None:
    """MiniMax models resolve to the seeded builtin provider as before."""
    await _add_zhipu(async_db)  # present but irrelevant to MiniMax models

    client = _make_client(async_db)
    await client.request("model.set_current", {"model": "MiniMax-M3"})

    pref = await ModelPrefsDAO(async_db).get_current()
    assert pref["model_id"] == "MiniMax-M3"
    assert pref["provider_id"] == "builtin-minimax"


# ---------------------------------------------------------------------------
# Resolution precedence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_provider_id_wins(async_db: AsyncDatabase) -> None:
    """An explicit provider_id overrides the reverse lookup."""
    glm = await _add_zhipu(async_db)

    client = _make_client(async_db)
    await client.request(
        "model.set_current",
        {"model": "glm-5.3", "provider_id": glm["id"]},
    )

    pref = await ModelPrefsDAO(async_db).get_current()
    assert pref["provider_id"] == glm["id"]


@pytest.mark.asyncio
async def test_duplicate_model_id_first_provider_by_name_wins(
    async_db: AsyncDatabase,
) -> None:
    """When two enabled providers expose the same model id, the one
    sorted first by name owns it (``ProviderDAO.list`` is ORDER BY name;
    ``list_models`` flattens in that order, and the handler keeps the
    first mapping it sees)."""
    prov_dao = ProviderDAO(async_db)
    first = await prov_dao.create(
        name="aaa-first",
        protocol="openai",
        base_url="https://first.invalid/v4",
        models=[{"id": "shared-model"}],
    )
    await prov_dao.create(
        name="zzz-second",
        protocol="openai",
        base_url="https://second.invalid/v4",
        models=[{"id": "shared-model"}],
    )

    client = _make_client(async_db)
    await client.request("model.set_current", {"model": "shared-model"})

    pref = await ModelPrefsDAO(async_db).get_current()
    assert pref["provider_id"] == first["id"]


@pytest.mark.asyncio
async def test_disabled_provider_model_is_rejected(
    async_db: AsyncDatabase,
) -> None:
    """A model offered only by a disabled provider stays unknown — the
    reverse lookup must not resurrect it (list_models skips disabled
    providers)."""
    prov_dao = ProviderDAO(async_db)
    await prov_dao.create(
        name="Zhipu",
        protocol="openai",
        base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        models=[{"id": "glm-5.3"}],
        enabled=False,
    )

    client = _make_client(async_db)
    with pytest.raises(RuntimeError) as ei:
        await client.request("model.set_current", {"model": "glm-5.3"})
    assert "unknown model" in str(ei.value).lower()

    # The preference row was not mutated.
    pref = await ModelPrefsDAO(async_db).get_current()
    assert pref["provider_id"] == "builtin-minimax"


# ---------------------------------------------------------------------------
# v1.6.3 field report — send_message must mirror the singleton's model
# ---------------------------------------------------------------------------


class _FakeContext:
    """Stand-in for :class:`Context` that captures reply / error."""

    def __init__(self, server: Any = None) -> None:
        self.reply_value: dict[str, Any] | None = None
        self.error_value: dict[str, Any] | None = None
        self.server = server

    async def reply(self, value: Any) -> None:
        self.reply_value = value

    async def reply_error(self, code: int, message: str, data: Any = None) -> None:
        self.error_value = {"code": code, "message": message, "data": data}

    async def emit(self, event: str, data: Any = None, **kw: Any) -> None:
        pass


class _FakeSessionsDAO:
    """Single-row sessions DAO — enough for the send_message prelude."""

    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row

    async def get(self, session_id: str) -> dict[str, Any] | None:
        return self._row if session_id == self._row["id"] else None


def _make_fake_db() -> AsyncMock:
    """Fake AsyncDatabase with awaitable fetch methods (p0-bugfixes pattern)."""
    db = AsyncMock()
    db.fetchall = AsyncMock(return_value=[])
    db.fetchone = AsyncMock(return_value=None)
    db.execute = AsyncMock()
    db.execute_insert = AsyncMock(return_value=None)
    return db


def _make_fake_runs_dao() -> MagicMock:
    dao = MagicMock()
    dao.create_run = AsyncMock(return_value={"id": "run_test"})
    dao.create_step = AsyncMock(return_value={"id": "step_test"})
    dao.complete_step = AsyncMock(return_value={"id": "step_test"})
    dao.update_run_status = AsyncMock(return_value={"id": "run_test"})
    return dao


def _send_message_patches(mock_core: MagicMock, llm: Any) -> dict[str, Any]:
    """The patch stack that drives ``handle_agent_send_message`` with a
    mocked AgentCore (pattern proven in test_workspace_scope_runs.py).

    ``AgentConfig`` itself is NOT patched, so the config captured by the
    ``AgentCore`` side_effect is the real dataclass the handler built.
    """
    sid = "sess_route_model"
    mock_result = MagicMock()
    mock_result.final_text = "done"
    mock_result.iterations = 1
    mock_result.usage = {}
    mock_core.run = AsyncMock(return_value=mock_result)
    captured: dict[str, Any] = {}

    def _capture_core(*args: Any, **kwargs: Any) -> MagicMock:
        captured.update(kwargs)
        return mock_core

    return {
        "sid": sid,
        "captured": captured,
        "patches": [
            patch("minimax_code.app.get_subagent_llm", return_value=llm),
            patch("minimax_code.app.init_runtime", return_value=MagicMock()),
            patch(
                "minimax_code.app.get_sessions_dao",
                return_value=_FakeSessionsDAO({"id": sid, "project_id": None}),
            ),
            patch("minimax_code.app.get_db", return_value=_make_fake_db()),
            patch(
                "minimax_code.storage.dao.runs.AgentRunsDAO",
                return_value=_make_fake_runs_dao(),
            ),
            patch("minimax_code.agent.AgentCore", side_effect=_capture_core),
            patch(
                "minimax_code.ipc.builtins._build_system_prompt_extra",
                return_value=None,
            ),
            patch(
                "minimax_code.perm_consent.PermissionGater",
                return_value=MagicMock(),
            ),
            patch(
                "minimax_code.ipc.handlers_permissions._ensure_permission_store",
                return_value=None,
            ),
        ],
    }


@pytest.mark.asyncio
async def test_send_message_mirrors_singleton_model_into_config() -> None:
    """The 1214 regression: with the singleton pointing at a third-party
    model, ``AgentConfig.model`` must follow it instead of the dataclass
    default ``"MiniMax-M3"`` (which the third-party endpoint rejects)."""
    mock_core = MagicMock()
    llm = SimpleNamespace(default_model="glm-5.3", mock=False)
    setup = _send_message_patches(mock_core, llm)

    ctx = _FakeContext(server=IPCServer(Config()))
    with ExitStack() as stack:
        for p in setup["patches"]:
            stack.enter_context(p)
        await handle_agent_send_message(
            {"content": "hi", "session_id": setup["sid"]}, ctx
        )

    assert ctx.error_value is None, ctx.error_value
    assert ctx.reply_value is not None
    config = setup["captured"]["config"]
    assert config.model == "glm-5.3"


@pytest.mark.asyncio
async def test_send_message_without_singleton_falls_back_to_default() -> None:
    """No singleton yet (fresh boot) → the bare fallback client's own
    default model fills the config; the field is never blank."""
    mock_core = MagicMock()
    setup = _send_message_patches(mock_core, llm=None)

    ctx = _FakeContext(server=IPCServer(Config()))
    with ExitStack() as stack:
        for p in setup["patches"]:
            stack.enter_context(p)
        await handle_agent_send_message(
            {"content": "hi", "session_id": setup["sid"]}, ctx
        )

    assert ctx.error_value is None, ctx.error_value
    config = setup["captured"]["config"]
    assert config.model == default_model()
