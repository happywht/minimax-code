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
