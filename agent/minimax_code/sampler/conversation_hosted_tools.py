"""Conversation-layer backend-hosted tools (R223, ``xai-grok-sampling-types``
``conversation.rs``).

R223 lands :class:`HostedTool` (``conversation.rs`` ~478) -- the conversation-
layer backend-hosted tool union from the "Tool Definitions and Calls" block,
**completing that block**: R222 landed the *client-side* :class:`ToolCall` +
:class:`ToolSpec` pair (a call the client executes + a function definition
offered to the model); :class:`HostedTool` is the *backend-side* peer (a tool
the backend executes server-side during inference). The client sends these as
native Responses API tool types (not Function definitions); the backend's
agentic sampler handles execution and streams results back. Two variants:
:class:`WebSearch` (a server-side web search with an optional domain
allowlist) + :class:`XSearch` (a server-side X/Twitter search, xAI-specific --
not part of the OpenAI Responses API, so it is injected as raw JSON into the
request body by the sampler client).

``#[derive(Debug, Clone)]`` only -- NO ``Serialize`` / ``Deserialize``, so this
is a pure **in-program enum** (it never crosses the wire itself and has no
``from_payload``). This is the same in-program-union shape as the R217
:class:`DanglingToolCallReason` (the ``conversation.rs`` first slice): a
frozen+slots union base + field-less / data-carrying subclass variants. The
R223 milestone is that :class:`HostedTool` is the first in-program union in
the package to carry a **method** (:meth:`wire_name`), so this round also
lands the "method on an in-program union" pattern -- the method lives on the
base and dispatches by ``isinstance`` (mirrors grok's single ``match self`` in
``impl HostedTool``; the same single-point-of-truth dispatch the R221
:class:`ContentPart` uses for :meth:`as_payload`).

:meth:`wire_name` returns the name the backend registers the tool under
server-side (``"web_search"`` / ``"x_search"``). The wire JSON for each
variant is emitted by the :class:`ConversationRequest` serializer (the
WebSearch -> ``{"type": "web_search", "allowed_domains": [...]}`` /
XSearch -> ``{"type": "x_search"}`` raw-JSON injection happens there, not
here) -- :class:`HostedTool` itself is an in-program value, not a wire type,
so R223 lands the union + the name predicate only; the serialization lands
with :class:`ConversationRequest` (which carries ``hosted_tools:
Vec<HostedTool>``).

Dependency closure: zero external. ``WebSearch.allowed_domains`` is
``Option<Vec<String>>`` -> ``tuple[str, ...] | None`` (the same option-of-
string-vec shape the R209 :class:`SearchSource` uses; a tuple, not a
frozenset, preserves the grok ``Vec`` ordering); ``XSearch`` carries no data.
No sampler types referenced. :class:`HostedTool` unblocks the
:class:`ConversationRequest` consumer layer (its ``hosted_tools`` field) for
a later slice -- the strategic reason this leaf lands before the other
zero-dependency ``conversation.rs`` struct leaves (``SystemItem`` /
``ToolResultItem`` sit on different axes).

No barrel collision: ``HostedTool`` / ``WebSearch`` / ``XSearch`` are free at
the package surface (no wire-layer peer -- the R206
:class:`ToolCallFunction` / R222 :class:`ToolSpec` / :class:`ToolCall` are all
client-side tool types, distinct names). Like the R217
:class:`DanglingToolCallReason` / R222 :class:`ToolCall`, no ``Conversation``
prefix is needed; the bare names mirror grok's own module-local ``HostedTool``
/ ``WebSearch`` / ``XSearch``.

This module is no-I/O (pure value-level). Migration map (grok -> Python):

- ``enum HostedTool { WebSearch { allowed_domains: Option<Vec<String>> },
  XSearch }`` -> :class:`HostedTool` frozen+slots union base +
  :class:`WebSearch` (``allowed_domains: tuple[str, ...] | None = None``) +
  :class:`XSearch` (field-less). ``Option<Vec<String>>`` ->
  ``tuple[str, ...] | None`` (immutable sequence, mirrors the R209
  :class:`SearchSource` precedent); ``Vec<String>`` is ordered so a tuple
  (not frozenset) preserves order. Defaulted to ``None`` (the common case --
  most web searches carry no allowlist restriction).
- ``#[derive(Debug, Clone)]`` (NO serde) -> no ``from_payload`` / no
  ``as_payload`` (the union is in-program; the wire JSON is emitted by the
  :class:`ConversationRequest` serializer, not here).
- ``impl HostedTool { fn wire_name(&self) -> &'static str }`` ->
  :meth:`HostedTool.wire_name`: base-class ``isinstance`` dispatch (single
  point of truth, mirrors grok's single ``match self``); :class:`WebSearch`
  -> ``"web_search"``, :class:`XSearch` -> ``"x_search"``, any other subclass
  -> ``TypeError`` (the base is never directly useful).

YAGNI: ``Serialize`` / ``Deserialize`` round-trip (grok itself does not derive
them -- the wire JSON is emitted inline by the :class:`ConversationRequest`
serializer); the :class:`ConversationRequest` consumer (it pulls in the
un-migrated :class:`ConversationItem` union + ``Vec<HostedTool>`` -- lands in
a later round).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HostedTool:
    """A tool the backend executes server-side during inference (union base).

    ``#[derive(Debug, Clone)]`` only -- NO serde, so this is a pure in-program
    enum (never crosses the wire itself, no ``from_payload``). The client sends
    these as native Responses API tool types (not Function definitions); the
    backend's agentic sampler handles execution and streams results back. Use
    a concrete variant: :class:`WebSearch` (server-side web search) or
    :class:`XSearch` (server-side X/Twitter search). The :meth:`wire_name`
    predicate lives on the base and dispatches by ``isinstance`` (mirrors
    grok's single ``match self`` in ``impl HostedTool``)."""

    def wire_name(self) -> str:
        """The name the backend registers this tool under server-side.

        Mirrors grok ``impl HostedTool { fn wire_name(&self) -> &'static str
        { match self { HostedTool::WebSearch { .. } => "web_search",
        HostedTool::XSearch => "x_search" } } }``: a single-point-of-truth
        ``isinstance`` dispatch (the same shape the R221
        :class:`ContentPart.as_payload` uses). :class:`WebSearch`` ->
        ``"web_search"``; :class:`XSearch` -> ``"x_search"``; any other
        subclass -> ``TypeError`` (the base is abstract-in-practice -- a
        bare :class:`HostedTool` carries no variant information)."""
        if isinstance(self, WebSearch):
            return "web_search"
        if isinstance(self, XSearch):
            return "x_search"
        raise TypeError(f"unknown HostedTool variant: {type(self).__name__}")


@dataclass(frozen=True, slots=True)
class WebSearch(HostedTool):
    """A server-side web search executed by the backend's agentic sampler.

    ``allowed_domains`` (grok ``Option<Vec<String>>``) is an optional domain
    allowlist for search results: ``None`` (grok ``Option::None``) means no
    allowlist restriction (search the whole web); a tuple of domain strings
    restricts results to those domains. Defaulted to ``None`` (the common
    case -- most web searches carry no allowlist). A tuple (not a frozenset)
    preserves the grok ``Vec`` ordering."""

    allowed_domains: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class XSearch(HostedTool):
    """A server-side X (Twitter) search executed by the backend's agentic
    sampler.

    xAI-specific -- not part of the OpenAI Responses API, so it is injected as
    raw JSON into the request body by the sampler client. Carries no data
    (grok unit variant)."""


__all__ = [
    "HostedTool",
    "WebSearch",
    "XSearch",
]
