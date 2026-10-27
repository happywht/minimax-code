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
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_run_service import Context
from test_run_service import native as native

from minimax_code.ipc.builtins import handle_agent_send_message
from minimax_code.ipc.client import IPCClient
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


@pytest.mark.asyncio
async def test_send_message_mirrors_singleton_model_into_config(native, monkeypatch) -> None:
    """The selected third-party model survives durable admission into AgentConfig."""
    from minimax_code import app

    provider = await _add_zhipu(native.db)
    llm = SimpleNamespace(default_model="glm-5.3", mock=False,
                          provider_id=provider["id"], protocol="openai")
    monkeypatch.setattr(app, "get_subagent_llm", lambda: llm)
    native.release.set()
    ctx = Context(native.server)
    await handle_agent_send_message({"content": "hi", "session_id": "one"}, ctx)

    ctx.reply_error.assert_not_awaited()
    receipt = ctx.reply.await_args.args[0]
    assert receipt["text"] == "done"
    assert len(native.cores) == 1
    assert native.cores[0].config.model == "glm-5.3"
    assert native.cores[0].llm is llm
    run = await native.dao.get_run(receipt["run_id"])
    assert run["status"] == "completed"
    assert run["metadata"]["model_snapshot"]["model"] == "glm-5.3"
    assert run["metadata"]["model_snapshot"]["provider_id"] == provider["id"]
    assert (await native.db.fetchone(
        "SELECT id FROM budget_families WHERE id=?",
        (run["metadata"]["budget_family_id"],),
    )) is not None


@pytest.mark.asyncio
async def test_send_message_without_initialized_runtime_refuses_execution(
    native, monkeypatch,
) -> None:
    """Failure to initialize cannot silently route to a default provider."""
    from minimax_code import app

    monkeypatch.setattr(app, "get_subagent_llm", lambda: None)
    ctx = Context(native.server)
    await handle_agent_send_message({"content": "hi", "session_id": "one"}, ctx)

    ctx.reply.assert_not_awaited()
    assert ctx.reply_error.await_args.args[0] == -32003
    assert "not initialized" in ctx.reply_error.await_args.args[1]
    assert not native.cores
    assert not await native.dao.list_runs()
    assert (await native.db.fetchone("SELECT COUNT(*) AS n FROM budget_families"))["n"] == 0
