"""Tests for the default-model registry — fusion of grok's ``xai-grok-models`` (R45).

Mirrors grok's contract (the baked-in JSON parses; the four accessors return
the default / scenario models with the scenario models falling back to the
default; the ``default ∈ models`` invariant is asserted at load) and pins the
Python mapping: the pydantic ``DefaultModels`` / ``DefaultModelEntry`` models,
the ``extra="ignore"`` behaviour (serde parity — display metadata is carried
in the JSON but not modelled), the ``lru_cache`` singleton, and the
localisation to MiniMax's three-model set.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from minimax_code import models as M

# --- baked-in document ------------------------------------------------------


def test_default_models_json_is_valid_json():
    """The baked-in constant is parseable JSON (grok include_str! payload)."""
    data = json.loads(M.DEFAULT_MODELS_JSON)
    assert "default" in data
    assert "models" in data
    assert isinstance(data["models"], list)
    assert data["models"]  # non-empty


def test_defaults_localised_to_minimax_three_model_set():
    """The baked-in set is MiniMax-M3 / M3-fast / MiniMax-Code (grok→MiniMax)."""
    d = M._load_defaults()
    ids = {entry.model for entry in d.models}
    assert ids == {"MiniMax-M3", "MiniMax-M3-fast", "MiniMax-Code"}
    assert d.default == "MiniMax-M3"


# --- accessors (grok default_model + three scenario models) -----------------


def test_default_model_returns_default():
    """grok default_model: the coding default."""
    assert M.default_model() == "MiniMax-M3"


def test_scenario_models_read_from_json_when_present():
    """Each scenario accessor returns its JSON value when set (no fallback)."""
    assert M.default_web_search_model() == "MiniMax-M3"
    assert M.default_image_description_model() == "MiniMax-M3"
    # session_summary uses the fast tier (lightweight task) — distinct from default.
    assert M.default_session_summary_model() == "MiniMax-M3-fast"


def test_scenario_models_fall_back_to_default(monkeypatch):
    """grok unwrap_or(&default): a missing scenario field falls back to default.

    Swaps the baked-in JSON for one with no scenario fields (all ``None``) and
    clears the ``lru_cache`` so the reload observes it; every accessor then
    yields the default. Restored + cache cleared on exit.
    """
    minimal = json.dumps(
        {"default": "MiniMax-M3", "models": [{"model": "MiniMax-M3"}]}
    )
    monkeypatch.setattr(M, "DEFAULT_MODELS_JSON", minimal)
    M._load_defaults.cache_clear()
    try:
        assert M.default_web_search_model() == "MiniMax-M3"
        assert M.default_image_description_model() == "MiniMax-M3"
        assert M.default_session_summary_model() == "MiniMax-M3"
    finally:
        M._load_defaults.cache_clear()


# --- invariant: default ∈ models (grok assert!) -----------------------------


def test_default_in_models_invariant_holds_for_baked_json():
    """The baked-in JSON satisfies the invariant (no AssertionError at load)."""
    d = M._load_defaults()
    assert d.default in {entry.model for entry in d.models}


def test_default_not_in_models_raises_assertion(monkeypatch):
    """grok panic: 'default' not in 'models' is a developer error.

    A baked-in document where the default is absent from the models list must
    raise :class:`AssertionError` on load (mirrors grok's ``assert!`` panic).
    """
    bad = json.dumps({"default": "missing", "models": [{"model": "MiniMax-M3"}]})
    monkeypatch.setattr(M, "DEFAULT_MODELS_JSON", bad)
    M._load_defaults.cache_clear()
    try:
        with pytest.raises(AssertionError):
            M._load_defaults()
    finally:
        M._load_defaults.cache_clear()


# --- pydantic mapping (serde parity) ----------------------------------------


def test_default_models_requires_default_field():
    """grok serde expect('missing default field') → pydantic ValidationError."""
    with pytest.raises(ValidationError):
        M.DefaultModels.model_validate({"models": [{"model": "X"}]})


def test_default_models_requires_models_field():
    """``models`` is required (grok struct field, no default)."""
    with pytest.raises(ValidationError):
        M.DefaultModels.model_validate({"default": "X"})


def test_default_model_entry_ignores_extra_fields():
    """serde 'ignore unknown fields' → pydantic v2 default extra='ignore'.

    The JSON carries display metadata (name / description / context_window /
    temperature / top_p); the entry models only ``model``, exactly as grok's
    struct, and the rest is dropped silently.
    """
    entry = M.DefaultModelEntry.model_validate(
        {
            "model": "MiniMax-M3",
            "name": "MiniMax-M3",
            "description": "flagship",
            "context_window": 200000,
            "temperature": 0.7,
            "top_p": 0.95,
        }
    )
    assert entry.model == "MiniMax-M3"
    # extra fields are not promoted to attributes (extra='ignore', not 'allow').
    assert not hasattr(entry, "name")
    assert not hasattr(entry, "context_window")


def test_default_models_optionals_default_none():
    """The three scenario fields are Optional, defaulting to None (grok Option)."""
    d = M.DefaultModels.model_validate(
        {"default": "X", "models": [{"model": "X"}]}
    )
    assert d.web_search is None
    assert d.image_description is None
    assert d.session_summary is None


# --- lru_cache singleton (grok LazyLock parity) -----------------------------


def test_load_defaults_is_cached_singleton():
    """grok LazyLock: the parse runs once — the same instance is returned."""
    M._load_defaults.cache_clear()
    first = M._load_defaults()
    second = M._load_defaults()
    assert first is second


# --- single-source wiring (R46) --------------------------------------------


def test_default_model_is_storage_single_source():
    """R46 wiring: storage's ``DEFAULT_MODEL`` derives from this registry.

    ``storage.dao.model_prefs.DEFAULT_MODEL`` is no longer a hard-coded
    literal — it is :func:`default_model` evaluated at import time, so the
    storage seed fallback, the DAO None-fallback, and this registry share
    one baked-in document (edit ``DEFAULT_MODELS_JSON`` to change the global
    default). This pins the wiring: a registry default change propagates to
    storage automatically, and a regression to a hard-coded literal breaks
    this test.
    """
    from minimax_code.storage.dao.model_prefs import DEFAULT_MODEL as storage_default

    assert storage_default == M.default_model()


# --- single-source wiring (R47) --------------------------------------------


def test_default_model_is_llm_client_single_source():
    """R47 wiring: the LLM client's ``DEFAULT_MODEL`` derives from this registry.

    ``minimax_code.agent.llm.DEFAULT_MODEL`` is no longer a hard-coded literal
    — it is :func:`default_model` evaluated at import time, so the client's
    ``MiniMaxClient(model=DEFAULT_MODEL)`` default and this registry share one
    baked-in document. This was the second of the two hard-coded
    ``DEFAULT_MODEL`` literals (R46 fixed ``model_prefs``); this pins the last
    one, and a regression to a hard-coded literal breaks this test.
    """
    from minimax_code.agent.llm import DEFAULT_MODEL as llm_default

    assert llm_default == M.default_model()


# --- candidate-set derivation (R48) ----------------------------------------


def test_default_model_ids_returns_ordered_three_model_set():
    """R48 vocabulary: ``default_model_ids`` lists every model in JSON order."""
    ids = M.default_model_ids()
    assert ids == ("MiniMax-M3", "MiniMax-M3-fast", "MiniMax-Code")
    assert isinstance(ids, tuple)  # ordered + indexable (callers use ids[0])


def test_default_model_ids_first_element_is_default():
    """R48 invariant: ``ids[0] == default_model()`` — pins the
    ``CANDIDATE_MODELS[0] == DEFAULT_MODEL`` contract that handlers + tests
    rely on (the JSON's ``models[0]`` is the flagship ``MiniMax-M3``)."""
    ids = M.default_model_ids()
    assert ids[0] == M.default_model()


def test_candidate_models_derived_from_vocabulary():
    """R48 wiring: handlers' ``CANDIDATE_MODELS`` is the vocabulary tuple.

    ``handlers_model.CANDIDATE_MODELS`` is no longer a hard-coded literal — it
    is :func:`default_model_ids`, so the candidate set and the default-model
    registry share one baked-in document. This pins the wiring; a regression to
    a hard-coded tuple breaks it.
    """
    from minimax_code.ipc.handlers_model import CANDIDATE_MODELS

    assert CANDIDATE_MODELS == M.default_model_ids()
    # The backward-compat invariant preserved by the derivation.
    assert CANDIDATE_MODELS[0] == M.default_model()


# --- scattered-fallback migration (R49) -------------------------------------


def test_resolve_subagent_parent_model_default_from_vocabulary():
    """R49 wiring: the resolver's ``parent_model`` default derives from registry.

    ``resolve_subagent_spec``'s ``parent_model`` was a hard-coded
    ``"MiniMax-M3"`` literal; it is now :func:`default_model` evaluated at
    definition time, so the sub-agent resolver and the default-model registry
    share one baked-in document. This is the first scattered ``"MiniMax-M3"``
    fallback migrated to the vocabulary (R49); a regression to a literal
    breaks this test.
    """
    import inspect

    from minimax_code.orchestrator.resolution import resolve_subagent_spec

    sig = inspect.signature(resolve_subagent_spec)
    assert sig.parameters["parent_model"].default == M.default_model()


def test_build_agent_core_model_fallback_from_vocabulary(monkeypatch):
    """R49 wiring: skill runtime's ``_build_agent_core`` model fallback uses registry.

    ``_build_agent_core``'s ``model or "MiniMax-M3"`` fallback was a hard-coded
    literal; it is now ``model or default_model()`` (lazy import to avoid the
    cold-start cycle), so the skill runtime and the default-model registry
    share one baked-in document. This is the second scattered ``"MiniMax-M3"``
    fallback migrated in R49. Monkeypatches ``AgentConfig`` / ``AgentCore`` to
    capture the resolved model without constructing a real core.
    """
    import types

    from minimax_code.agent import core as core_mod
    from minimax_code.agent.skills.runtime import _build_agent_core

    captured: dict[str, object] = {}

    class _FakeConfig:
        def __init__(self, **kwargs: object) -> None:
            captured["model"] = kwargs.get("model")

    class _FakeCore:
        def __init__(self, **kwargs: object) -> None:
            captured["core"] = kwargs

    monkeypatch.setattr(core_mod, "AgentConfig", _FakeConfig)
    monkeypatch.setattr(core_mod, "AgentCore", _FakeCore)

    fake_skill = types.SimpleNamespace(instructions="test-instructions")
    # model=None → the fallback path resolves to default_model().
    _build_agent_core(
        llm=None,
        tool_registry=None,
        skill=fake_skill,
        model=None,
        max_iterations=None,
    )
    assert captured["model"] == M.default_model()
    # An explicit model is passed through unchanged (fallback not triggered).
    _build_agent_core(
        llm=None,
        tool_registry=None,
        skill=fake_skill,
        model="explicit-model",
        max_iterations=None,
    )
    assert captured["model"] == "explicit-model"


# --- core default wiring (R50, milestone) -----------------------------------


def test_agent_config_model_default_from_vocabulary():
    """R50 wiring (milestone): ``AgentConfig.model`` default derives from registry.

    ``AgentConfig.model`` was a hard-coded ``"MiniMax-M3"`` dataclass field
    default — the central conversation-loop config. It is now
    :func:`default_model` evaluated at class-definition time, so the agent
    core and the default-model registry share one baked-in document. This is
    the core-layer capstone of the single-source migration (R46 storage →
    R47 llm → R48 candidate set → R49 resolver/runtime → R50 AgentConfig); a
    regression to a literal breaks this test.
    """
    import dataclasses

    from minimax_code.agent.core import AgentConfig

    # The field default itself (not a default_factory) is the vocabulary value.
    model_field = next(
        field for field in dataclasses.fields(AgentConfig) if field.name == "model"
    )
    assert model_field.default == M.default_model()
    # And a default-constructed AgentConfig reflects it.
    assert AgentConfig().model == M.default_model()


# --- completion route fallback (R51) ----------------------------------------


async def test_build_llm_client_model_fallback_from_vocabulary(monkeypatch):
    """R51 wiring: completion route's ``_build_llm_client`` model fallback uses registry.

    ``_build_llm_client``'s ``model_name or "MiniMax-M3"`` fallback (the
    provider branch) was a hard-coded literal; it is now
    ``model_name or default_model()`` (lazy import — the function already
    lazy-imports ``get_db`` / the DAOs to avoid the app↔routes cold-start
    cycle), so the inline-completion route and the default-model registry
    share one baked-in document. Monkeypatches the DAO/provider chain +
    ``MiniMaxClient`` to capture the resolved model without a real DB.
    """
    from minimax_code import app as app_mod
    from minimax_code.agent import completion_routes as routes
    from minimax_code.storage.dao import model_prefs, providers

    captured: dict[str, list[dict[str, object]]] = {"clients": []}
    pref_model: dict[str, object] = {"model": None}  # mutable so we can flip it

    class _FakeMiniMaxClient:
        def __init__(self, **kwargs: object) -> None:
            captured["clients"].append(kwargs)

    class _FakePrefsDAO:
        def __init__(self, db: object) -> None:
            pass

        async def get(self) -> dict[str, object] | None:
            m = pref_model["model"]
            return None if m is None else {"model": m}

    class _FakeProviderDAO:
        def __init__(self, db: object) -> None:
            pass

        async def get_active(self) -> dict[str, str]:
            return {"protocol": "anthropic", "api_key": "k", "base_url": "u"}

    monkeypatch.setattr(routes, "MiniMaxClient", _FakeMiniMaxClient)
    monkeypatch.setattr(app_mod, "get_db", lambda: object())  # non-None db
    monkeypatch.setattr(model_prefs, "ModelPrefsDAO", _FakePrefsDAO)
    monkeypatch.setattr(providers, "ProviderDAO", _FakeProviderDAO)

    # Branch 1: model_name=None → provider branch falls back to default_model().
    pref_model["model"] = None
    await routes._build_llm_client(server=None)
    assert captured["clients"][0]["model"] == M.default_model()

    # Branch 2: model_name="explicit" → passthrough (the ``or`` honours truthy).
    pref_model["model"] = "explicit-model"
    await routes._build_llm_client(server=None)
    assert captured["clients"][1]["model"] == "explicit-model"
