"""Stored reasoning effort is loaded onto a client and published atomically.

The asynchronous loader must never change the currently selected effort before
its provider/client is ready. Native admission captures that published selection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from minimax_code.orchestrator.subagent import (
    SubAgentConfig,
    SubAgentRuntime,
    get_subagent_runtime,
    set_subagent_runtime,
)
from minimax_code.storage.dao.model_prefs import ModelPrefsDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_process_singletons():
    """Process-global singletons must not leak between tests.

    ``_REASONING_EFFORT_OVERRIDE`` (R62) and the ``SubAgentRuntime`` singleton
    are module-level globals mutated by the code under test. Without a reset,
    a test that sets ``"high"`` would poison every later test in the run. We
    snapshot + restore both, mirroring how ``test_subagent_spawn`` handles the
    runtime singleton.
    """
    import minimax_code.app as app

    prev_llm = app.get_subagent_llm()
    prev_effort = app.get_reasoning_effort_override()
    prev_runtime = get_subagent_runtime()
    app._set_reasoning_effort_override(None)
    yield
    app.set_subagent_llm(prev_llm)
    app._set_reasoning_effort_override(prev_effort)
    set_subagent_runtime(prev_runtime)


def _cfg() -> SubAgentConfig:
    """A minimal valid sub-agent config (name + system_prompt are required)."""
    return SubAgentConfig(name="worker", system_prompt="you are a worker agent")


# ---------------------------------------------------------------------------
# Singleton getter / setter (the cache itself)
# ---------------------------------------------------------------------------


def test_effort_override_defaults_none():
    """Fresh process state: no override cached (the pre-R62 behaviour)."""
    import minimax_code.app as app

    assert app.get_reasoning_effort_override() is None


def test_effort_override_round_trip():
    """Setter writes; getter reads back the same canonical token."""
    import minimax_code.app as app

    app._set_reasoning_effort_override("high")
    assert app.get_reasoning_effort_override() == "high"


def test_effort_override_clears_on_none():
    """``None`` reverts the cache to the no-override state."""
    import minimax_code.app as app

    app._set_reasoning_effort_override("high")
    app._set_reasoning_effort_override(None)
    assert app.get_reasoning_effort_override() is None


# ---------------------------------------------------------------------------
# _rebuild_subagent_llm forwards the stored effort into the singleton
# ---------------------------------------------------------------------------
#
# Loading is side-effect free; publish only after all configuration reads finish.


@pytest.mark.asyncio
async def test_rebuild_loads_stored_effort_into_singleton(tmp_path: Path):
    """A stored override surfaces in the singleton after a rebuild."""
    import minimax_code.app as app

    db = AsyncDatabase(make_temp_database_path(tmp_path))
    await db.connect()
    await db.migrate()
    try:
        dao = ModelPrefsDAO(db)
        await dao.set_reasoning_effort("xhigh")
        assert app.get_reasoning_effort_override() is None  # before rebuild
        app._set_subagent_llm(await app._rebuild_subagent_llm(db))
        assert app.get_reasoning_effort_override() == "xhigh"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_rebuild_clears_singleton_when_override_cleared(tmp_path: Path):
    """Clearing the stored override (None) is reflected on the next rebuild.

    Without this, a user who cleared their effort override would still see the
    stale value on the next turn. The singleton is a *derived* view of the DB,
    refreshed on every rebuild — not an independent store.
    """
    import minimax_code.app as app

    db = AsyncDatabase(make_temp_database_path(tmp_path))
    await db.connect()
    await db.migrate()
    try:
        dao = ModelPrefsDAO(db)
        await dao.set_reasoning_effort("high")
        app._set_subagent_llm(await app._rebuild_subagent_llm(db))
        assert app.get_reasoning_effort_override() == "high"
        await dao.set_reasoning_effort(None)
        app._set_subagent_llm(await app._rebuild_subagent_llm(db))
        assert app.get_reasoning_effort_override() is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_rebuild_effort_set_even_when_provider_missing(tmp_path: Path):
    """Unavailable selections retain their effort without becoming executable."""
    import minimax_code.app as app

    db = AsyncDatabase(make_temp_database_path(tmp_path))
    await db.connect()
    await db.migrate()
    try:
        dao = ModelPrefsDAO(db)
        await dao.set_reasoning_effort("low")
        await db.execute("DELETE FROM providers")
        # Publish an unavailable selection, preserving its requested effort.
        app._set_subagent_llm(await app._rebuild_subagent_llm(db))
        assert app.get_reasoning_effort_override() == "low"
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# SubAgentRuntime construction threads effort into AgentConfig
# ---------------------------------------------------------------------------


def test_subagent_runtime_default_effort_none():
    """No effort at construction ⇒ AgentConfig.reasoning_effort is None.

    Zero-regression guarantee: a runtime built with no override behaves
    byte-identically to the pre-R62 path (the model's own default effort).
    """
    rt = SubAgentRuntime()
    handle = rt.build(_cfg())
    assert handle.core.config.reasoning_effort is None


def test_subagent_runtime_threads_effort_into_config():
    """A construction-time effort reaches the built AgentConfig verbatim."""
    rt = SubAgentRuntime(reasoning_effort="high")
    handle = rt.build(_cfg())
    assert handle.core.config.reasoning_effort == "high"


def test_subagent_runtime_setter_hot_swaps_effort():
    """``set_reasoning_effort`` updates the runtime in place (no rebuild needed).

    The client is effort-agnostic (effort is a per-call ``stream_chat`` param,
    not a constructor field), so a hot-swap here takes effect on the next
    ``build`` without reconnecting httpx. This is the seam
    ``model.set_reasoning_effort`` → ``rebuild_subagent_llm`` drives.
    """
    rt = SubAgentRuntime(reasoning_effort="low")
    handle = rt.build(_cfg())
    assert handle.core.config.reasoning_effort == "low"
    rt.set_reasoning_effort("xhigh")
    handle2 = rt.build(_cfg())
    assert handle2.core.config.reasoning_effort == "xhigh"


# ---------------------------------------------------------------------------
# _set_subagent_llm wires the singleton into the runtime (timing invariant)
# ---------------------------------------------------------------------------
#
# The boot path is ``_set_subagent_llm(await _rebuild_subagent_llm(db))`` and
# the rebuild path is the same two calls in sequence. In both, the rebuild
# (which writes the singleton) runs *before* the setter (which reads it) — so
# the setter can rely on the singleton being current without any DB call of
# its own. These tests pin that the wiring honours the timing rather than
# reading a stale value.


def test_set_subagent_llm_threads_singleton_into_runtime():
    """Boot path: singleton → SubAgentRuntime singleton carries the effort."""
    import minimax_code.app as app
    from minimax_code.agent.llm import MiniMaxClient

    app._set_reasoning_effort_override("high")
    app._set_subagent_llm(MiniMaxClient(mock=True))
    runtime = get_subagent_runtime()
    assert runtime is not None
    assert runtime._reasoning_effort == "high"
    # And a sub-agent built from the runtime inherits it end-to-end.
    handle = runtime.build(_cfg())
    assert handle.core.config.reasoning_effort == "high"


def test_set_subagent_llm_no_override_yields_none():
    """No override cached ⇒ runtime effort is None (zero regression)."""
    import minimax_code.app as app
    from minimax_code.agent.llm import MiniMaxClient

    app._set_reasoning_effort_override(None)
    app._set_subagent_llm(MiniMaxClient(mock=True))
    runtime = get_subagent_runtime()
    assert runtime._reasoning_effort is None


# ---------------------------------------------------------------------------
# End-to-end: DB → rebuild → set_subagent_llm → SubAgentRuntime.build
# ---------------------------------------------------------------------------
#
# The full R62 chain a real boot runs. This is the contract R62 adds over R61:
# a persisted override (written by ``model.set_reasoning_effort``) now produces
# a sub-agent whose AgentConfig carries the effort, so the next turn's
# ``stream_chat`` (R55) forwards it to the transport (R56/R57 emit on wire).


@pytest.mark.asyncio
async def test_end_to_end_stored_effort_reaches_subagent_config(tmp_path: Path):
    """Persisted override → runtime singleton → built AgentConfig.

    The boot-path ordering is exercised for real here: ``_rebuild_subagent_llm``
    loads the effort onto the client, then ``_set_subagent_llm`` publishes
    it into the freshly-built runtime. The DB stores what the handler
    canonicalised (R61); the runtime forwards it verbatim — no re-canonicalisation
    at this layer (the wire emit happens downstream via R56/R57).
    """
    import minimax_code.app as app

    db = AsyncDatabase(make_temp_database_path(tmp_path))
    await db.connect()
    await db.migrate()
    try:
        dao = ModelPrefsDAO(db)
        await dao.set_reasoning_effort("high")
        # Boot path: publish the completely loaded client and effort together.
        client = await app._rebuild_subagent_llm(db)
        app._set_subagent_llm(client)
        runtime = get_subagent_runtime()
        assert runtime._reasoning_effort == "high"
        handle = runtime.build(_cfg())
        assert handle.core.config.reasoning_effort == "high"
    finally:
        await db.close()
