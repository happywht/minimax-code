"""API backend selector (R214, ``xai-grok-sampling-types`` ``types.rs`` 1010-1030).

R214 lands ``ApiBackend`` -- a zero-dependency 3-variant snake_case
wire-string enum that selects which xAI API backend a model uses for
inference (Chat Completions / Responses / Anthropic Messages), plus its
single decision method :meth:`supports_native_schema`. It is the direct
``api_backend`` field of :class:`SamplingConfig` (the sampling-client
configuration that lands later); the enum itself is a clean standalone
leaf.

Dependency note (correcting an earlier deferral): the R206 docstring listed
``ApiBackend`` / ``SamplingConfig`` together as "depend on ``crate::rs``".
That conflates the two -- ``ApiBackend`` (``types.rs`` 1010-1030) has NO
``crate::rs`` / ``xai-grok-tools`` / ``crate::serde_helpers`` /
``indexmap`` / ``NonZeroU64`` dependency. Only ``SamplingConfig``
(``types.rs`` 1032-1055) carries the ``indexmap::IndexMap`` +
``NonZeroU64`` deps (and consumes ``ApiBackend`` as its ``#[serde(default)]
api_backend`` field). R214 migrates the independent leaf now; the consumer
lands later.

This module is no-I/O (``serde_json::Value`` -> wire string).
Migration map (grok -> Python):

- ``#[serde(rename_all="snake_case")] enum`` -> :class:`enum.StrEnum` with
  the snake_case wire values: ``ChatCompletions`` -> ``"chat_completions"``
  (the ``/v1/chat/completions`` endpoint); ``Responses`` -> ``"responses"``
  (``/v1/responses``); ``Messages`` -> ``"messages"`` (``/v1/messages``, the
  Anthropic Messages API).
- ``#[default] ChatCompletions`` -> the :data:`DEFAULT_API_BACKEND` constant.
- ``impl ApiBackend { fn supports_native_schema(&self) -> bool }`` -> the
  :meth:`supports_native_schema` method (mirror grok
  ``matches!(self, Self::ChatCompletions | Self::Responses)``).

Strict enum parse: grok's ``ApiBackend`` carries NO ``#[serde(other)]``
catch-all, so an unknown wire string or a non-string raises ``ValueError``
(mirrors serde's enum failure on this catch-all-less enum -- consistent
with the R206 :class:`Role` / :class:`ReasoningEffort` strict parsers).
:meth:`from_payload` is the strict single-string parser; the enum is also
directly constructable via ``ApiBackend("chat_completions")`` (the
inherited :class:`enum.StrEnum` value lookup).

Keystone -- the ``supports_native_schema`` decision: the Messages API does
NOT enforce a response JSON schema natively alongside tool calls (a schema
there blocks tool use), so structured output on the Messages backend goes
through the StructuredOutput tool instead. Chat Completions + Responses DO
enforce it natively. This asymmetry is the single behavioral reason the
enum exists as more than a string -- it gates the structured-output
dispatch in the sampling client.

YAGNI: full serde ``Serialize`` / ``Deserialize`` round-trip --
:meth:`from_payload` covers the parse direction; :func:`str` covers the
serialize direction (the :class:`enum.StrEnum` value IS the wire string,
so ``str(ApiBackend.MESSAGES)`` yields ``"messages"`` -- ``as_str`` has no
Python peer beyond :func:`str`). The :class:`SamplingConfig` consumer
(field ``api_backend`` with ``#[serde(default)]``) lands later; until then
the default :data:`DEFAULT_API_BACKEND` stands in for it.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["DEFAULT_API_BACKEND", "ApiBackend"]


class ApiBackend(StrEnum):
    """Which xAI API backend to use for model inference
    (``#[serde(rename_all="snake_case")]``).

    ``ChatCompletions`` -> ``"chat_completions"`` (the
    ``/v1/chat/completions`` endpoint, the default); ``Responses`` ->
    ``"responses"`` (``/v1/responses``); ``Messages`` -> ``"messages"``
    (``/v1/messages``, the Anthropic Messages API).
    """

    CHAT_COMPLETIONS = "chat_completions"
    RESPONSES = "responses"
    MESSAGES = "messages"

    @classmethod
    def from_payload(cls, raw: object) -> ApiBackend:
        """Strict wire-string parser (no catch-all -- an unknown backend or
        non-string raises ``ValueError``, mirroring serde's enum failure on
        this ``#[serde(other)]``-less enum)."""
        if not isinstance(raw, str):
            raise ValueError(
                f"api_backend wire value must be a string, got {type(raw).__name__}"
            )
        try:
            return cls(raw)
        except ValueError as exc:
            raise ValueError(f"unknown api_backend wire value: {raw!r}") from exc

    def supports_native_schema(self) -> bool:
        """Whether the backend enforces a response JSON schema natively
        alongside tool calls.

        Mirror grok ``matches!(self, Self::ChatCompletions | Self::Responses)``:
        Chat Completions + Responses -> ``True``; the Messages API -> ``False``
        (a schema there blocks tool use, so structured output goes through the
        StructuredOutput tool instead).
        """
        return self in (ApiBackend.CHAT_COMPLETIONS, ApiBackend.RESPONSES)


DEFAULT_API_BACKEND = ApiBackend.CHAT_COMPLETIONS
"""Mirror of grok ``#[default] ApiBackend::ChatCompletions`` (the default
backend, also the ``#[serde(default)]`` value of the ``api_backend`` field
on :class:`SamplingConfig``)."""
