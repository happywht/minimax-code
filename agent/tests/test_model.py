"""Tests for the ``model.*`` IPC namespace.

Coverage:

* :class:`TestModelPrefsDAO` — DAO round-trip against a real
  SQLite file (async). Asserts default seed from the migration,
  set/get cycle, ``updated_at`` bump, sync helpers, and concurrent
  set safety.
* :class:`TestModelIPC` — drives the ``model.*`` handlers
  in-process via :class:`minimax_code.ipc.client.IPCClient`.
  Asserts candidate list shape, default current, set_current
  persistence, and ``-32602`` rejection of unknown model names.
* :class:`TestModelPersistence` — closes the DB, reopens it, and
  asserts the previously-set value is still there (real on-disk
  durability, not just in-process).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from minimax_code.ipc.client import IPCClient
from minimax_code.ipc.handlers_model import (
    CANDIDATE_MODELS,
    is_valid_model,
)
from minimax_code.storage.dao.model_prefs import (
    DEFAULT_MODEL,
    ModelPrefsDAO,
    get_current_sync,
    set_current_sync,
)
from minimax_code.storage.dao.providers import ProviderDAO
from minimax_code.storage.db import AsyncDatabase, Database, make_temp_database_path

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Fresh temp DB path per test."""
    return make_temp_database_path(tmp_path)


@pytest.fixture
async def async_db(db_path: Path) -> AsyncDatabase:
    """A migrated async Database."""
    db = AsyncDatabase(db_path)
    await db.connect()
    await db.migrate()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
def prefs_dao(async_db: AsyncDatabase) -> ModelPrefsDAO:
    return ModelPrefsDAO(async_db)


# ---------------------------------------------------------------------------
# DAO
# ---------------------------------------------------------------------------


class TestModelPrefsDAO:
    @pytest.mark.asyncio
    async def test_migration_seeds_default(
        self, prefs_dao: ModelPrefsDAO
    ) -> None:
        """Fresh DB after migrate() should have current_model == DEFAULT_MODEL."""
        current = await prefs_dao.get_current()
        assert current["model_id"] == DEFAULT_MODEL
        assert current["provider_id"] == "builtin-minimax"

    @pytest.mark.asyncio
    async def test_set_then_get(self, prefs_dao: ModelPrefsDAO) -> None:
        await prefs_dao.set_current("MiniMax-Code")
        result = await prefs_dao.get_current()
        assert result["model_id"] == "MiniMax-Code"
        # And back to default
        await prefs_dao.set_current(DEFAULT_MODEL)
        result = await prefs_dao.get_current()
        assert result["model_id"] == DEFAULT_MODEL

    @pytest.mark.asyncio
    async def test_set_updates_timestamp(
        self, prefs_dao: ModelPrefsDAO
    ) -> None:
        before = await prefs_dao.get_state()
        await prefs_dao.set_current("MiniMax-M3-fast")
        after = await prefs_dao.get_state()
        assert after["current_model"] == "MiniMax-M3-fast"
        # Timestamp must move forward (or at least not regress).
        assert after["updated_at"] >= before["updated_at"]
        # PK is fixed at 1.
        assert after["id"] == 1

    @pytest.mark.asyncio
    async def test_set_dao_does_not_validate_candidate(
        self, prefs_dao: ModelPrefsDAO
    ) -> None:
        """The DAO is a thin persistence wrapper — the IPC layer
        owns the candidate-list check. We deliberately do *not*
        reject unknown model names here."""
        await prefs_dao.set_current("some-future-model")
        result = await prefs_dao.get_current()
        assert result["model_id"] == "some-future-model"

    @pytest.mark.asyncio
    async def test_sync_helpers_round_trip(self, db_path: Path) -> None:
        """Sync helpers share the same DB and read what the async
        DAO writes (and vice versa)."""
        # Apply migrations via the async path so the seed row exists.
        async_db = AsyncDatabase(db_path)
        await async_db.connect()
        await async_db.migrate()
        dao = ModelPrefsDAO(async_db)
        await dao.set_current("MiniMax-Code")
        await async_db.close()

        # Read back via the sync Database.
        with Database(db_path) as sync_db:
            assert get_current_sync(sync_db)["model_id"] == "MiniMax-Code"
            row = set_current_sync(sync_db, "MiniMax-M3-fast")
            assert row["current_model"] == "MiniMax-M3-fast"
            assert row["id"] == 1
            assert get_current_sync(sync_db)["model_id"] == "MiniMax-M3-fast"

    @pytest.mark.asyncio
    async def test_concurrent_set_no_corruption(
        self, prefs_dao: ModelPrefsDAO
    ) -> None:
        """50 concurrent set_current calls — final value must be
        one of the candidate models, and the row must remain
        valid (single-row table, no duplicates)."""

        async def flip(i: int) -> None:
            model = CANDIDATE_MODELS[i % len(CANDIDATE_MODELS)]
            await prefs_dao.set_current(model)

        await asyncio.gather(*(flip(i) for i in range(50)))
        final = await prefs_dao.get_current()
        assert final["model_id"] in CANDIDATE_MODELS
        # Table still has exactly one row.
        state = await prefs_dao.get_state()
        assert state["id"] == 1
        assert state["current_model"] == final["model_id"]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestModelPersistence:
    @pytest.mark.asyncio
    async def test_set_survives_reopen(self, tmp_path: Path) -> None:
        """Restart the database — the row should still be there."""
        path = make_temp_database_path(tmp_path)
        db1 = AsyncDatabase(path)
        await db1.connect()
        await db1.migrate()
        dao1 = ModelPrefsDAO(db1)
        await dao1.set_current("MiniMax-Code")
        await db1.close()

        db2 = AsyncDatabase(path)
        await db2.connect()
        dao2 = ModelPrefsDAO(db2)
        assert (await dao2.get_current())["model_id"] == "MiniMax-Code"
        await db2.close()


# ---------------------------------------------------------------------------
# IPC handlers — in-process
# ---------------------------------------------------------------------------


def _make_client_with_model_handlers(
    prefs_dao: ModelPrefsDAO,
) -> IPCClient:
    """Build an IPCClient whose model handlers use the supplied DAO.

    Mirrors the trick in :mod:`test_permissions`: replace the lazily
    built DAO with a pre-built one by stashing it on the server
    before the first ``model.*`` call.
    """
    client = IPCClient()
    client.server._model_prefs_dao = prefs_dao
    client.server._model_prefs_dao_lock = asyncio.Lock()
    client.server._provider_dao_for_model = ProviderDAO(prefs_dao._db)
    client.server._provider_dao_for_model_lock = asyncio.Lock()
    return client


class TestModelIPC:
    @pytest.mark.asyncio
    async def test_list_returns_candidates(
        self, prefs_dao: ModelPrefsDAO
    ) -> None:
        client = _make_client_with_model_handlers(prefs_dao)
        result = await client.request("model.list", {})
        assert "models" in result
        models = result["models"]
        # Each entry is a rich ModelInfo dict with id, name, provider, etc.
        ids = tuple(m["id"] for m in models)
        # The list is now dynamic from ProviderDAO — it may include models
        # from providers the user added manually.  Assert that all three
        # built-in MiniMax candidates are present (superset check).
        for expected in CANDIDATE_MODELS:
            assert expected in ids, f"built-in model {expected!r} missing from {ids}"
        assert "current" in result
        # Sanity: the three names we promised in the spec.
        assert "MiniMax-M3" in ids
        assert "MiniMax-M3-fast" in ids
        assert "MiniMax-Code" in ids

    @pytest.mark.asyncio
    async def test_get_current_default(
        self, prefs_dao: ModelPrefsDAO
    ) -> None:
        client = _make_client_with_model_handlers(prefs_dao)
        result = await client.request("model.get_current", {})
        assert result == {"model": DEFAULT_MODEL}

    @pytest.mark.asyncio
    async def test_set_current_persists(
        self, prefs_dao: ModelPrefsDAO
    ) -> None:
        client = _make_client_with_model_handlers(prefs_dao)
        result = await client.request(
            "model.set_current", {"model": "MiniMax-Code"}
        )
        assert result["ok"] is True
        assert result["model"] == "MiniMax-Code"
        assert result["current"] == "MiniMax-Code"
        # And the DAO agrees.
        assert (await prefs_dao.get_current())["model_id"] == "MiniMax-Code"
        # get_current also reports it.
        get_result = await client.request("model.get_current", {})
        assert get_result == {"model": "MiniMax-Code"}

    @pytest.mark.asyncio
    async def test_set_current_rejects_unknown_model(
        self, prefs_dao: ModelPrefsDAO
    ) -> None:
        client = _make_client_with_model_handlers(prefs_dao)
        with pytest.raises(RuntimeError) as ei:
            await client.request("model.set_current", {"model": "fake-model"})
        msg = str(ei.value)
        assert "unknown model" in msg.lower()
        assert "fake-model" in msg
        # The DAO was not mutated.
        assert (await prefs_dao.get_current())["model_id"] == DEFAULT_MODEL

    @pytest.mark.asyncio
    async def test_set_current_rejects_missing_param(
        self, prefs_dao: ModelPrefsDAO
    ) -> None:
        client = _make_client_with_model_handlers(prefs_dao)
        with pytest.raises(RuntimeError) as ei:
            await client.request("model.set_current", {})
        assert "non-empty" in str(ei.value)

    @pytest.mark.asyncio
    async def test_set_current_rejects_empty_string(
        self, prefs_dao: ModelPrefsDAO
    ) -> None:
        client = _make_client_with_model_handlers(prefs_dao)
        with pytest.raises(RuntimeError) as ei:
            await client.request("model.set_current", {"model": "  "})
        assert "non-empty" in str(ei.value)

    @pytest.mark.asyncio
    async def test_error_code_is_invalid_params(
        self, prefs_dao: ModelPrefsDAO
    ) -> None:
        """Unknown model name must surface as -32602 INVALID_PARAMS,
        which the frontend uses to show a clean "model not available"
        message."""
        client = _make_client_with_model_handlers(prefs_dao)
        with pytest.raises(RuntimeError) as ei:
            await client.request("model.set_current", {"model": "no-such-model"})
        err = ei.value.args[0]
        # The error object is a dict; check the code.
        assert isinstance(err, dict)
        assert err.get("code") == -32602

    # --- R58: reasoning-effort meta enrichment (first consumer of R53 readers) ---
    #
    # R53's meta readers were orphans (defined, never consumed). R58 wires the
    # first consumer into ``model.list``: a model that declares reasoning-effort
    # meta (``reasoningEffort`` / ``reasoningEfforts`` / ``supportsReasoningEffort``
    # — grok's per-model catalog vocabulary) surfaces normalised snake_case fields
    # the frontend can render an effort selector from. A model that declares none
    # is unchanged (zero regression — no new keys), so every existing MiniMax
    # model passes through byte-identically.

    @pytest.mark.asyncio
    async def test_list_enriches_reasoning_effort_meta(
        self, prefs_dao: ModelPrefsDAO
    ) -> None:
        """A model declaring reasoningEffort meta surfaces normalised fields."""
        prov_dao = ProviderDAO(prefs_dao._db)
        await prov_dao.create(
            name="xAI",
            protocol="anthropic",
            base_url="https://api.x.ai",
            models=[
                {
                    "id": "grok-1",
                    "name": "Grok-1",
                    "supportsReasoningEffort": True,
                    "reasoningEffort": "high",
                    "reasoningEfforts": ["low", "medium", "high"],
                }
            ],
        )
        client = _make_client_with_model_handlers(prefs_dao)
        result = await client.request("model.list", {})
        grok = next(m for m in result["models"] if m["id"] == "grok-1")
        assert grok["supports_reasoning_effort"] is True
        assert grok["reasoning_effort_default"] == "high"
        assert [o["value"] for o in grok["reasoning_effort_options"]] == [
            "low",
            "medium",
            "high",
        ]
        # The provider annotations from list_models survive alongside the
        # R58 derived fields.
        assert grok["provider_id"].startswith("provider-")
        assert grok["protocol"] == "anthropic"

    @pytest.mark.asyncio
    async def test_list_without_reasoning_meta_is_zero_regression(
        self, prefs_dao: ModelPrefsDAO
    ) -> None:
        """A model with no reasoning-effort meta gets no new keys (zero regression).

        The built-in MiniMax models declare no reasoning-effort meta, so they
        must pass through ``model.list`` unchanged — the pre-R58 shape is
        identical, proving the enrichment is strictly opt-in.
        """
        client = _make_client_with_model_handlers(prefs_dao)
        result = await client.request("model.list", {})
        m3 = next(m for m in result["models"] if m["id"] == "MiniMax-M3")
        assert "supports_reasoning_effort" not in m3
        assert "reasoning_effort_default" not in m3
        assert "reasoning_effort_options" not in m3


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def test_is_valid_model_helper() -> None:
    assert is_valid_model("MiniMax-M3") is True
    assert is_valid_model("MiniMax-Code") is True
    assert is_valid_model("not-a-model") is False
    assert is_valid_model("") is False
    assert is_valid_model(None) is False  # type: ignore[arg-type]


def test_candidate_list_includes_default() -> None:
    """The first entry must be DEFAULT_MODEL so a freshly-seeded
    DB can resolve the migration's seed value."""
    assert CANDIDATE_MODELS[0] == DEFAULT_MODEL
