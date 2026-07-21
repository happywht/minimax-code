"""Sampling-client configuration (R216, ``xai-grok-sampling-types`` ``types.rs`` 1032-1055).

R216 lands :class:`SamplingConfig` -- the sampling-client configuration
container that holds the non-secret knobs a sampling client needs (the API
key stays in the client, NOT here). It is the single ``types.rs`` leaf that
consumes BOTH the R214 :class:`ApiBackend` (its ``#[serde(default)]
api_backend`` field) AND the R206 :class:`ReasoningEffort` (its optional
``reasoning_effort`` field) -- R214 + R206 were the runway, R216 is the
takeoff. It also resolves the two grok deps that previously blocked this
leaf (and that the R206 / R214 docstrings flagged as the deferral reason):
``indexmap::IndexMap`` + ``NonZeroU64``.

Dependency closure: zero external. The two blocking deps map cleanly onto
Python builtins:

- ``indexmap::IndexMap<String, String>`` (``extra_headers``) -> a tuple of
  ``(key, value)`` string pairs. Python 3.7+ ``dict`` is insertion-ordered
  (IndexMap's core guarantee), but a ``tuple[tuple[str, str], ...]`` is
  immutable + hashable -- consistent with every other frozen+slots field in
  the package, and it lets the whole config stay hashable. The insertion
  order is preserved (``tuple(d.items())``); non-string keys/values are
  skipped (a malformed header never crashes the parse). O(1) key lookup is
  YAGNI -- the platform only iterates the headers to inject them into an
  HTTP request.
- ``NonZeroU64`` (``context_window``) -> a plain ``int`` with a ``> 0``
  invariant enforced in :meth:`from_payload` (a missing / non-int / bool /
  ``<= 0`` value raises ``ValueError``, mirroring serde's ``NonZeroU64``
  deserialize failure on ``0``). The ``NonZero`` marker is a Rust type-level
  guarantee with no Python peer -- the runtime check is the faithful
  translation.

The other ``types.rs`` 1030-1521 candidates remain blocked:
``CreateResponseWrapper`` / ``MessagesRequestWrapper`` depend on
``crate::rs::CreateResponse`` + ``crate::messages::MessagesRequest`` +
``Box<dyn TraceContext>`` (YAGNI -- the trace trait pulls in the ``tracing``
crate); ``ChatCompletionRequest`` still carries three un-landed deps
(``ToolDefinition`` + ``crate::rs::ResponseFormat`` + ``Box<dyn TraceContext>``).
:class:`SamplingConfig` is the only zero-dependency leaf left in that band.

This module is no-I/O (``serde_json::Value`` -> ``dict`` / wire value).
Migration map (grok -> Python):

- ``pub base_url: String`` / ``pub model: String`` (required, no
  ``#[serde(default)]``) -> required ``str`` fields; a missing / non-string
  value raises ``ValueError`` (mirrors serde's missing-required-field failure).
- ``pub context_window: NonZeroU64`` (required, ``> 0`` invariant) -> a
  required ``int``; a missing / non-int / bool / ``<= 0`` value raises
  ``ValueError`` (mirrors ``NonZeroU64`` deserialize failure on ``0``; ``bool``
  is an ``int`` subclass in Python but grok ``u64`` rejects a JSON ``true``).
- ``#[serde(default)] pub api_backend: ApiBackend`` -> missing / ``None`` ->
  :data:`DEFAULT_API_BACKEND`; a present value parses strictly through
  :meth:`ApiBackend.from_payload` (an unknown backend raises, mirroring
  serde's enum failure on this catch-all-less enum).
- ``max_completion_tokens: Option<u32>`` -> ``int | None`` via
  :func:`_optional_int` (an ``int`` that is NOT a ``bool`` -> the value;
  anything else -> ``None``).
- ``temperature: Option<f32>`` / ``top_p: Option<f32>`` -> ``float | None``
  via :func:`_optional_float` (an ``int`` or ``float`` that is NOT a ``bool``
  -> the value kept as-is; grok ``f32`` accepts JSON integers; anything else
  -> ``None``).
- ``extra_headers: IndexMap<String, String>`` (``#[serde(default,
  skip_serializing_if="IndexMap::is_empty")]``) -> ``tuple[tuple[str, str],
  ...]`` via :func:`_ordered_string_pairs` (a JSON object -> a tuple of its
  string-keyed / string-valued items in insertion order, non-string items
  skipped; missing / null / non-object -> the empty tuple).
- ``reasoning_effort: Option<ReasoningEffort>`` (``#[serde(default,
  skip_serializing_if="Option::is_none")]``) -> ``ReasoningEffort | None``: a
  string value parses strictly through :meth:`ReasoningEffort.from_payload`
  (an unknown effort raises); missing / null / non-string -> ``None``.
- ``stream_tool_calls: Option<bool>`` -> ``bool | None``: a ``bool`` value is
  kept; anything else -> ``None``.

Naming: the container keeps its grok name verbatim -- it has no Anthropic
Messages API peer (the R205 :class:`MessagesRequest` is the Anthropic
request body, structurally distinct).

YAGNI: full serde ``Serialize``/``Deserialize`` round-trip --
:meth:`from_payload` covers the parse direction the platform needs. The
secret ``api_key`` stays in the client (grok docstring: "API key excluded --
that stays in the client"); the platform's secret layer (``secrets.py`` +
OS keyring) owns it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from minimax_code.sampler.api_backend import DEFAULT_API_BACKEND, ApiBackend
from minimax_code.sampler.chat_completion_leaves import ReasoningEffort


def _optional_int(value: Any) -> int | None:
    """Parse an ``Option<u32>`` wire value: an ``int`` that is NOT a ``bool``
    -> the value; anything else (``None``, a ``bool``, a ``float``, a string)
    -> ``None``. ``bool`` is an ``int`` subclass in Python but grok ``u32``
    rejects a JSON ``true`` -- the explicit exclusion mirrors that."""
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _optional_float(value: Any) -> float | None:
    """Parse an ``Option<f32>`` wire value: an ``int`` or ``float`` that is
    NOT a ``bool`` -> the value (kept as-is -- grok ``f32`` accepts JSON
    integers); anything else -> ``None``."""
    if isinstance(value, bool):
        return None
    return value if isinstance(value, (int, float)) else None


def _ordered_string_pairs(value: Any) -> tuple[tuple[str, str], ...]:
    """Parse an ``IndexMap<String, String>`` wire value (``extra_headers``):
    a JSON object -> a tuple of its string-keyed / string-valued items in
    insertion order (non-string keys/values skipped, so a malformed header
    never crashes the parse); missing / null / non-object -> the empty tuple
    (mirrors ``#[serde(default)]``). Python 3.7+ ``dict`` preserves insertion
    order, so ``tuple(d.items())`` is the IndexMap order."""
    if isinstance(value, dict):
        return tuple(
            (key, val)
            for key, val in value.items()
            if isinstance(key, str) and isinstance(val, str)
        )
    return ()


@dataclass(frozen=True, slots=True)
class SamplingConfig:
    """Sampling-client configuration (``types.rs`` 1032-1055).

    Holds the non-secret knobs a sampling client needs (the API key stays in
    the client). ``base_url`` / ``model`` / ``context_window`` are required;
    the rest are optional. Use :meth:`from_payload` (a non-dict payload
    raises; ``context_window`` must be a positive ``int``; ``base_url`` /
    ``model`` must be strings)."""

    base_url: str
    model: str
    context_window: int
    api_backend: ApiBackend = DEFAULT_API_BACKEND
    max_completion_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    extra_headers: tuple[tuple[str, str], ...] = ()
    reasoning_effort: ReasoningEffort | None = None
    stream_tool_calls: bool | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> SamplingConfig:
        """Tolerant-of-optionals constructor with strict requireds.

        ``base_url`` / ``model`` are required strings (missing / non-string ->
        ``ValueError``); ``context_window`` is a required positive ``int``
        (missing / non-int / bool / ``<= 0`` -> ``ValueError``, mirroring
        ``NonZeroU64`` deserialize failure); ``api_backend`` defaults to
        :data:`DEFAULT_API_BACKEND` when missing / null, else parses strictly;
        ``max_completion_tokens`` / ``temperature`` / ``top_p`` /
        ``stream_tool_calls`` / ``reasoning_effort`` are optional with the
        tolerances documented at the module top; ``extra_headers`` defaults to
        the empty tuple. A non-dict payload raises ``ValueError``."""
        if not isinstance(payload, dict):
            raise ValueError(
                f"sampling config must be a dict, got {type(payload).__name__}"
            )
        raw_base = payload.get("base_url")
        if not isinstance(raw_base, str):
            raise ValueError(
                f"base_url must be a string, got {type(raw_base).__name__}"
            )
        raw_model = payload.get("model")
        if not isinstance(raw_model, str):
            raise ValueError(
                f"model must be a string, got {type(raw_model).__name__}"
            )
        raw_window = payload.get("context_window")
        if isinstance(raw_window, bool) or not isinstance(raw_window, int):
            raise ValueError(
                f"context_window must be a positive integer, "
                f"got {type(raw_window).__name__}"
            )
        if raw_window <= 0:
            raise ValueError(f"context_window must be > 0, got {raw_window}")
        raw_backend = payload.get("api_backend")
        if raw_backend is None:
            api_backend = DEFAULT_API_BACKEND
        else:
            api_backend = ApiBackend.from_payload(raw_backend)
        raw_effort = payload.get("reasoning_effort")
        reasoning_effort = (
            ReasoningEffort.from_payload(raw_effort)
            if isinstance(raw_effort, str)
            else None
        )
        raw_stream = payload.get("stream_tool_calls")
        stream_tool_calls = raw_stream if isinstance(raw_stream, bool) else None
        return cls(
            base_url=raw_base,
            model=raw_model,
            context_window=raw_window,
            api_backend=api_backend,
            max_completion_tokens=_optional_int(payload.get("max_completion_tokens")),
            temperature=_optional_float(payload.get("temperature")),
            top_p=_optional_float(payload.get("top_p")),
            extra_headers=_ordered_string_pairs(payload.get("extra_headers")),
            reasoning_effort=reasoning_effort,
            stream_tool_calls=stream_tool_calls,
        )


__all__ = ["SamplingConfig"]
