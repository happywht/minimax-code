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
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

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
