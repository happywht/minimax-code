"""401 attribution callback hook (pure) -- fusion of grok's ``xai-grok-sampler``
(R233, ``src/attribution.rs`` whole-leaf migration, crate deepening round).

``xai-grok-sampler`` is grok's actor-based sampling/inference layer (HTTP
streaming + retry, no shell coupling). The crate's ``attribution.rs`` ships
four symbols, all with zero external crate dependency (only ``std::sync::Arc``
in the original); this module migrates the whole leaf faithfully and **clears
the ``SamplerConfig`` deferred-dependency ledger entry** recorded in
:mod:`minimax_code.sampler.config` (the ``attribution::SharedAttributionCallback
(unmigrated)`` line). The remaining ``SamplerConfig`` blockers (``retry`` /
``sampling_types`` sub-types / ``HeaderMap``) are unchanged.

Migration map (grok Rust -> platform Python)
--------------------------------------------

* ``enum SamplingConsumer`` (6 variants, ``#[derive(Debug, Clone, Copy,
  PartialEq, Eq)]``) -> :class:`SamplingConsumer` (:class:`enum.StrEnum`,
  6 members). The member *value* is the endpoint identifier string (so the
  enum round-trips through the wire value), but :meth:`as_endpoint` is kept
  as an explicit method mirroring grok's ``pub fn as_endpoint(&self) ->
  &'static str`` -- the endpoint identifier and the serde value are the same
  today, but the two are conceptually distinct and may diverge; the method
  shields every call site from that future split.
* ``impl SamplingConsumer { fn as_endpoint }`` -> :meth:`SamplingConsumer
  .as_endpoint` (returns ``str(self)``, i.e. the ``StrEnum`` value).
* ``pub const SENT_BEARER_PREFIX_LEN: usize = 12`` ->
  :data:`SENT_BEARER_PREFIX_LEN` (``int`` with annotation). Cross-crate
  invariant mirror of ``xai_grok_shell::auth::token_suffix`` (12 chars); the
  boundary is load-bearing only here, so the constant lives with the
  callback that consumes it.
* ``trait Auth401AttributionCallback: Send + Sync + Debug { fn record_401 }``
  -> :class:`Auth401AttributionCallback` (:class:`abc.ABC` +
  :meth:`@abstractmethod record_401`). The ``Send + Sync`` bounds are
  meaningless in Python (no thread-affinity for pure objects); the
  ``Debug`` bound was a *structural* Rust requirement (``SamplerConfig``
  derives ``Debug`` and carries an ``Option<Arc<dyn ...>>`` field) -- Python
  objects are ``repr``-able by default, so no analogue is needed (recorded
  in the class docstring for fidelity).
* ``type SharedAttributionCallback = Arc<dyn Auth401AttributionCallback>`` ->
  :data:`SharedAttributionCallback` (:data:`typing.TypeAlias`). Python
  objects are shared by reference natively (assignment does not copy), so
  there is no ``Arc`` equivalent -- the alias records the semantic intent
  ("a cheaply-cloneable shared callback") without a runtime wrapper.

Scope: sampler endpoints only
-----------------------------

This enum enumerates the six HTTP endpoints owned by ``SamplingClient``
(chat completions, responses, messages -- each in streaming and
non-streaming form). It does *not* cover image / video / web-search /
embedding -- those tools live in ``xai-grok-tools`` and hook into a
separate ``ApiKeyProvider`` trait. Kept here verbatim for fidelity.
"""

from __future__ import annotations

import abc
from enum import StrEnum
from typing import TypeAlias


class SamplingConsumer(StrEnum):
    """A logical 401-emitting site inside the sampling client (grok
    ``SamplingConsumer``).

    The string identifier ends up in the ``consumer`` field of the
    attribution event so downstream queries can break down 401s by API
    path. The member *value* is the endpoint identifier string (mirrors
    grok's ``as_endpoint`` return), so the enum serializes / compares
    against the wire value directly.

    Scope: sampler endpoints only (see module docstring). The six
    ``SamplingClient`` HTTP endpoints, streaming + non-streaming each.
    """

    #: ``chat_completion_stream``: OpenAI-compatible streaming Chat Completions API.
    CHAT_COMPLETIONS_STREAM = "chat_completions_stream"
    #: ``chat_completion``: OpenAI-compatible non-streaming Chat Completions API.
    CHAT_COMPLETIONS = "chat_completions"
    #: ``create_response_stream``: Responses API streaming.
    RESPONSES_STREAM = "responses_stream"
    #: ``create_response``: Responses API non-streaming.
    RESPONSES = "responses"
    #: ``messages_stream``: Anthropic Messages API streaming.
    MESSAGES_STREAM = "messages_stream"
    #: ``messages``: Anthropic Messages API non-streaming.
    MESSAGES = "messages"

    def as_endpoint(self) -> str:
        """Stable string identifier for this emit site (grok ``as_endpoint``).

        Callbacks typically combine this with a fixed prefix (e.g. the
        client type) when building the ``consumer`` field of the
        attribution event. Today the identifier equals the ``StrEnum``
        value; kept as an explicit method so the two concepts (endpoint
        identifier vs serde value) can diverge without touching call
        sites.
        """
        return str(self)


#: Maximum prefix length the sampler shares with attribution callbacks across
#: the crate boundary (grok ``SENT_BEARER_PREFIX_LEN``). Mirrors
#: ``xai_grok_shell::auth::token_suffix`` (which truncates to 12 chars before
#: any sink) so the two crates stay in lock-step on the "bearers leaving the
#: sampler are 12-char prefixes only" invariant. Changing it requires updating
#: ``token_suffix`` in ``xai-grok-shell/src/auth/manager.rs`` to match.
SENT_BEARER_PREFIX_LEN: int = 12


class Auth401AttributionCallback(abc.ABC):
    """Hook invoked by ``SamplingClient`` at every 401 response site (grok
    ``Auth401AttributionCallback`` trait).

    Implementations are responsible for joining ``sent_bearer_prefix`` with
    whatever live credential source they own (e.g. an auth manager holding
    the most-recently-refreshed token) and emitting whatever attribution
    event makes sense for their observability stack.

    Implementations must be cheap to invoke and must not block. They run
    inside the request's response-handling path and any latency they add is
    paid by the user-visible 401 error path.

    Note on the dropped ``Debug`` bound: in Rust the ``Debug`` bound on the
    trait was a *structural* requirement (``SamplerConfig`` derives ``Debug``
    and carries an ``Option<Arc<dyn Auth401AttributionCallback>>`` field,
    which only compiles when the trait is ``Debug``). Python objects are
    ``repr``-able by default, so no analogue is needed here -- but any
    concrete subclass that intends to ride on a future ``SamplerConfig``
    ``__repr__`` should keep a readable ``__repr__``.
    """

    @abc.abstractmethod
    def record_401(
        self,
        consumer: SamplingConsumer,
        sent_bearer_prefix: str | None,
    ) -> None:
        """Record a 401 attribution event for one logical 401 response.

        ``sent_bearer_prefix`` is the **first :data:`SENT_BEARER_PREFIX_LEN`
        characters** of the bearer that was actually sent on the wire. The
        sampler extracts the bearer from the ``Authorization`` header (or
        ``x-api-key`` for Anthropic Messages API backends) and truncates it
        to the prefix length **before crossing this trait boundary** -- the
        full bearer never leaves ``SamplingClient``. This is the
        scrub-at-the-boundary invariant: even a misbehaving callback
        implementation that logs ``sent_bearer_prefix`` directly leaks only
        the prefix, never the full credential.

        ``None`` indicates the request had no bearer header at all (distinct
        from "had a bearer that turned out to be stale").
        """
        raise NotImplementedError


#: Shared, cheap-to-clone alias for the attribution callback (grok
#: ``SharedAttributionCallback = Arc<dyn Auth401AttributionCallback>``).
#: Python objects are shared by reference natively (no ``Arc`` runtime
#: wrapper needed); the alias records the semantic intent.
SharedAttributionCallback: TypeAlias = Auth401AttributionCallback


__all__ = [
    "Auth401AttributionCallback",
    "SENT_BEARER_PREFIX_LEN",
    "SamplingConsumer",
    "SharedAttributionCallback",
]
