"""Default model IDs — fusion of grok's ``xai-grok-models`` (R45).

A data-driven default-model registry: a baked-in JSON document
(:data:`DEFAULT_MODELS_JSON`) parsed once into a pydantic model
(:class:`DefaultModels`), with a ``default ∈ models`` invariant asserted at
load and four scenario accessors (the coding default plus web-search /
image-description / session-summary models that fall back to the default).
Mirrors grok's ``xai-grok-models`` (lib.rs, 71 lines): "Default model IDs for
the grok CLI, loaded from the embedded default_models.json."

Mapping
-------
* ``DEFAULT_MODELS_JSON`` (``include_str!("../default_models.json")`` — baked
  into the binary at compile time) → :data:`DEFAULT_MODELS_JSON`, a module-level
  string literal. Python has no compile-time file embedding; a source-level
  constant is the faithful runtime equivalent (edit the constant + restart ≈
  edit the JSON + recompile). The JSON is localised to MiniMax's three-model
  set (``MiniMax-M3`` / ``MiniMax-M3-fast`` / ``MiniMax-Code``); grok's
  ``api_backend`` / ``supported_in_api`` fields are xAI-specific and dropped.
* ``DefaultModels`` (``#[derive(serde::Deserialize)]``, fields ``default`` +
  three ``Option<String>`` + ``models: Vec<DefaultModelEntry>``) →
  :class:`DefaultModels`, a pydantic v2 ``BaseModel`` (branch (a) of the payload
  decision tree — the serialisation layer — same as R40 ``QueueEntryMeta``).
  serde's "ignore unknown fields by default" → pydantic v2's default
  ``extra="ignore"``: the JSON's ``name`` / ``description`` / ``context_window``
  / ``temperature`` / ``top_p`` metadata is carried in the document for future
  consumers but is not modelled here, exactly as grok's struct reads only
  ``model``.
* ``DefaultModelEntry`` (``#[derive(serde::Deserialize)]``, single ``model``
  field) → :class:`DefaultModelEntry`, pydantic v2 ``BaseModel`` (extra fields
  ignored, mirroring grok's struct).
* ``static DEFAULTS: LazyLock<DefaultModels>`` (parse once + ``assert!(default ∈
  models)`` + thread-safe) → :func:`_load_defaults`, wrapped in
  :func:`@functools.lru_cache(maxsize=1) <functools.lru_cache>` (single
  initialisation, thread-safe under the GIL, mirrors ``LazyLock``).
* ``default_model`` / ``default_web_search_model`` /
  ``default_image_description_model`` / ``default_session_summary_model`` →
  module-level functions of the same name; ``Option::unwrap_or(&default)`` →
  ``or default``.
* grok's ``serde`` / ``serde_json`` crate deps → :mod:`json` (stdlib) +
  :mod:`pydantic` (already an agent dependency). The resolve precedence in
  grok's doc comment (CLI flag > env > config.toml > remote settings > these
  defaults) is **not** in this crate (it lives in grok's ``agent::config``);
  this module is the defaults leaf only — wiring the precedence is a future
  round (YAGNI).

Product fusion
--------------
MiniMax Code's :mod:`handlers_model` hard-codes the candidate set
(``CANDIDATE_MODELS`` / ``MODEL_META``) for backward compatibility while the
live list is read from ``ProviderDAO``. This module is the **defaults
vocabulary** — a single baked-in document, scenario-tagged (web-search /
image-description / session-summary each pick the right tier), with an
invariant that the default is one of the listed models. The IPC handler's
adoption of these defaults (so ``model.get_current`` falls back through them)
is a future round; for now this is the leaf they build on.
"""

from __future__ import annotations

import functools
import json

from pydantic import BaseModel

#: The baked-in default-models document (grok ``include_str!`` compile-time
#: embedding → a source-level string constant). Edit this constant to change
#: the defaults (the Python analogue of editing ``default_models.json`` and
#: recompiling). Localised to MiniMax's three-model set; the ``web_search`` /
#: ``image_description`` scenarios use the flagship ``MiniMax-M3`` (synthesis
#: needs the stronger model) and ``session_summary`` uses ``MiniMax-M3-fast``
#: (a summary is lightweight — the fast tier is enough and cheaper).
DEFAULT_MODELS_JSON: str = """{
  "default": "MiniMax-M3",
  "web_search": "MiniMax-M3",
  "image_description": "MiniMax-M3",
  "session_summary": "MiniMax-M3-fast",
  "models": [
    {
      "model": "MiniMax-M3",
      "name": "MiniMax-M3",
      "description": "Flagship model for advanced coding and reasoning",
      "context_window": 200000,
      "temperature": 0.7,
      "top_p": 0.95
    },
    {
      "model": "MiniMax-M3-fast",
      "name": "MiniMax-M3-fast",
      "description": "Fast, cost-efficient model for lightweight tasks",
      "context_window": 128000,
      "temperature": 0.7,
      "top_p": 0.95
    },
    {
      "model": "MiniMax-Code",
      "name": "MiniMax-Code",
      "description": "Coding specialist with a 1M-token context window",
      "context_window": 1000000,
      "temperature": 0.7,
      "top_p": 0.95
    }
  ]
}
"""


class DefaultModelEntry(BaseModel):
    """One entry in the default-models list (grok ``DefaultModelEntry``).

    Only the ``model`` ID is modelled — pydantic v2's default ``extra="ignore"``
    drops the JSON's display metadata (``name`` / ``description`` /
    ``context_window`` / …), mirroring grok's serde struct (which reads only
    ``model``; the metadata is for other consumers).
    """

    model: str


class DefaultModels(BaseModel):
    """The parsed default-models document (grok ``DefaultModels``).

    ``default`` + the three optional scenario models that fall back to it, plus
    the ``models`` list. Unknown JSON fields are ignored (pydantic default),
    matching serde.
    """

    default: str
    web_search: str | None = None
    image_description: str | None = None
    session_summary: str | None = None
    models: list[DefaultModelEntry]


@functools.lru_cache(maxsize=1)
def _load_defaults() -> DefaultModels:
    """Parse :data:`DEFAULT_MODELS_JSON` once and assert the invariant.

    Mirrors grok's ``static DEFAULTS: LazyLock<DefaultModels>``: a single
    initialisation (``lru_cache(maxsize=1)`` caches the result; thread-safe
    under the GIL) plus the ``assert!(default ∈ models)`` developer check. A
    mismatch here is a developer error (the JSON is baked in), so an
    :class:`AssertionError` is the faithful analogue of grok's panic.
    """
    defaults = DefaultModels.model_validate(json.loads(DEFAULT_MODELS_JSON))
    model_ids = [entry.model for entry in defaults.models]
    assert defaults.default in model_ids, (
        f"DEFAULT_MODELS_JSON: 'default' is {defaults.default!r} "
        f"but 'models' only has {model_ids!r}"
    )
    return defaults


def default_model() -> str:
    """Primary model for coding tasks and general fallback (grok ``default_model``)."""
    return _load_defaults().default


def default_web_search_model() -> str:
    """Model for web-search tool synthesis; falls back to the default (grok)."""
    d = _load_defaults()
    return d.web_search or d.default


def default_image_description_model() -> str:
    """Model for image description; falls back to the default (grok)."""
    d = _load_defaults()
    return d.image_description or d.default


def default_session_summary_model() -> str:
    """Model for session-title generation; falls back to the default (grok)."""
    d = _load_defaults()
    return d.session_summary or d.default


__all__ = [
    "DEFAULT_MODELS_JSON",
    "DefaultModelEntry",
    "DefaultModels",
    "default_image_description_model",
    "default_model",
    "default_session_summary_model",
    "default_web_search_model",
]
