"""W3C traceparent context propagation (R129).

Fusion of grok-build's ``xai-tracing/src/fastrace.rs`` — *the pure-logic
subset*. The full Rust module is a heavy fastrace / opentelemetry / tonic /
reqwest integration layer; this round lands only the symbols that need none
of those backends, establishing the :class:`SpanContext` abstraction that
the later leaves (``tokio`` task propagation, ``http_client`` request
injection, ``grpc_client`` middleware) build on.

Symbols landing this round
--------------------------

* :class:`SpanContext` — a ``(trace_id, span_id, trace_flags)`` triple that
  encodes to / decodes from a W3C traceparent string. It is the Python
  counterpart of fastrace's ``SpanContext``.
* :meth:`SpanContext.encode_w3c_traceparent` /
  :meth:`SpanContext.decode_w3c_traceparent` — the W3C traceparent
  codec. ``decode`` also folds in ``xai-tracing/src/testing.rs``'s
  ``parse_traceparent`` logic (split on ``-``) plus the W3C validity rules
  the test helper omits (version field, all-zero trace/span id).
* :meth:`SpanContext.random` — mint a fresh random context
  (``SpanContext::random``).
* :func:`current_trace_id` — the W3C traceparent of the context active in
  the current :mod:`contextvars` scope, or ``None``
  (``current_trace_id``).
* :func:`local_or_random_span_ctx` — the current context, or a fresh random
  one if none is active (``local_or_random_span_ctx``).
* :func:`enter_span_with_traceparent` — a context manager that decodes a
  traceparent and makes it the active context for the duration of the
  ``with`` block (``enter_span_with_traceparent``).

fastrace::Span -> contextvars current SpanContext
-------------------------------------------------

Rust's fastrace ``Span`` is a *lifecycle* object: a named span rooted at
either a decoded traceparent or the local parent, started on construction
and finished on drop, maintaining a span tree. Python has no such object
here — this landing keeps only the **trace-context propagation** half: a
single :class:`SpanContext` held in a :class:`~contextvars.ContextVar`, set
on ``with enter_span_with_traceparent(...)`` and reset on exit via the
saved :class:`~contextvars.Token`. No span tree, no name lifecycle, no
start/finish events. This is a deliberate YAGNI downgrade recorded here,
not silently taken: MiniMax Code's need is traceparent propagation to
outbound requests (the ``http_client`` leaf consumes this), not a full
distributed-tracing span tree, and pulling opentelemetry-sdk for the latter
would violate YAGNI today.

Consequence for ``enter_span_with_traceparent``: in Rust an *invalid*
traceparent falls back to ``Span::enter_with_local_parent(name)`` (a new
child of whatever span is locally active). In Python the equivalent of
"local parent" is the current :class:`SpanContext`, so "do not change
current" is the faithful behaviour — the context manager returns ``None``
from ``__enter__`` and leaves the active context untouched, which is
exactly "stay on the local parent".

contextvars, not thread-local
-----------------------------

Rust's ``SpanContext::current_local_parent()`` reads the thread-local
fastrace collector. Python's :mod:`contextvars` is the faithful
counterpart and is also what :mod:`asyncio` propagates automatically: a
task spawned with :func:`asyncio.create_task` inherits a copy of the
current context, so a :class:`SpanContext` set in one coroutine is visible
to the tasks it spawns. That is precisely the contract the ``tokio`` leaf
(``spawn_traced``) will express next round, and it is why this module uses
:class:`~contextvars.ContextVar` rather than :mod:`threading`.

Deferred to later rounds (YAGNI)
-------------------------------

* ``init_fastrace(endpoint, name, attrs)`` — the OTLP/gRPC reporter setup.
  Needs ``opentelemetry-sdk`` + an OTLP backend; MiniMax Code has none
  configured, so landing it would be dead code.
* ``TraceparentMiddleware`` — the reqwest HTTP middleware. Lands with the
  ``http_client`` leaf (it is the consumer of :func:`current_trace_id`).
* ``FastraceChannel`` / ``fastrace_channel`` — the tonic gRPC trace layer.
  Lands with the ``grpc_client`` leaf.
"""

from __future__ import annotations

import contextvars
import re
import secrets
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import TracebackType

__all__ = [
    "SpanContext",
    "current_trace_id",
    "local_or_random_span_ctx",
    "enter_span_with_traceparent",
]

#: W3C traceparent wire format:
#: ``version "-" trace-id "-" parent-id "-" trace-flags``, where version is
#: 2 hex digits, trace-id 32 hex, parent-id (span-id) 16 hex, flags 2 hex.
#: Anchored so a trailing segment or stray byte will not match.
_TRACEPARENT_RE = re.compile(
    r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$"
)

#: W3C traceparent version this module emits. ``00`` is the only defined
#: version today; decoding still accepts other versions byte-for-byte
#: (a future-version traceparent round-trips through decode/encode) but the
#: encoder always writes ``00``.
_TRACE_VERSION = "00"

#: Default trace-flags byte: bit 0 (the W3C "sampled" flag) set, all others
#: clear. Matches fastrace's ``SpanContext::random`` default disposition.
_TRACE_FLAGS_SAMPLED = 0x01

#: The :class:`~contextvars.ContextVar` holding the active
#: :class:`SpanContext` for the current async/thread context. ``None`` means
#: "no trace context active" — :func:`current_trace_id` returns ``None`` and
#: :func:`local_or_random_span_ctx` mints a fresh one. :mod:`asyncio`
#: propagates this automatically across spawned tasks.
_current_span_context: contextvars.ContextVar[SpanContext | None] = (
    contextvars.ContextVar("minimax_code_tracing_span_context", default=None)
)


class SpanContext:
    """A W3C trace context triple (R129).

    Holds the ``(trace_id, span_id, trace_flags)`` needed to format a W3C
    traceparent. ``trace_id`` is 32 lower-case hex chars, ``span_id`` 16,
    ``trace_flags`` an int in ``[0, 255]``. Construct directly only with
    values you have already validated (e.g. decoded from a trusted
    traceparent); otherwise prefer :meth:`random` or
    :meth:`decode_w3c_traceparent`, which enforce the W3C format.

    Instances are immutable in practice (no method mutates fields) and
    hashable by identity — two contexts with identical fields are *not*
    considered equal, because in distributed tracing two spans carrying the
    same wire bytes are still distinct spans. Compare by
    :meth:`encode_w3c_traceparent` if wire-equality is what you mean.
    """

    __slots__ = ("trace_id", "span_id", "trace_flags")

    def __init__(
        self,
        trace_id: str,
        span_id: str,
        trace_flags: int = _TRACE_FLAGS_SAMPLED,
    ) -> None:
        """Store the triple verbatim; no validation here (caller's duty).

        :meth:`decode_w3c_traceparent` and :meth:`random` are the validated
        constructors. Constructing directly with bad hex is undefined
        behaviour at the encode step, mirroring how fastrace trusts its own
        internal construction.
        """
        self.trace_id = trace_id
        self.span_id = span_id
        self.trace_flags = trace_flags

    @classmethod
    def random(cls) -> SpanContext:
        """Mint a fresh context with random trace-id and span-id (Rust ``random``).

        ``trace_id`` is 16 random bytes (32 hex); ``span_id`` 8 random bytes
        (16 hex); both via :mod:`secrets` so they are suitable as trace
        identifiers. ``trace_flags`` defaults to the sampled disposition.
        """
        return cls(secrets.token_hex(16), secrets.token_hex(8))

    def encode_w3c_traceparent(self) -> str:
        """Format as a W3C traceparent string (Rust ``encode_w3c_traceparent``).

        Returns ``{version}-{trace_id}-{span_id}-{trace_flags:02x}`` —
        version ``00``, trace_flags zero-padded to two hex digits. The
        inverse of :meth:`decode_w3c_traceparent` for any context this
        module produces.
        """
        return (
            f"{_TRACE_VERSION}-{self.trace_id}-{self.span_id}"
            f"-{self.trace_flags:02x}"
        )

    @classmethod
    def decode_w3c_traceparent(cls, traceparent: str) -> SpanContext | None:
        """Parse a W3C traceparent, or return ``None`` if malformed.

        Folds in ``xai-tracing/src/testing.rs``'s ``parse_traceparent``
        (split on ``-`` into version / trace-id / span-id) plus the W3C
        validity rules the test helper omits: the regex anchors the exact
        field widths, and an all-zero ``trace-id`` or ``span-id`` is
        rejected (W3C §3.3.1.1 / §3.3.1.2 forbid them). ``None`` (rather
        than raising) mirrors fastrace's ``decode_w3c_traceparent`` return
        type and lets :func:`enter_span_with_traceparent` fall back to the
        local parent on a bad header.
        """
        match = _TRACEPARENT_RE.match(traceparent)
        if match is None:
            return None
        _version, trace_id, span_id, flags_hex = match.groups()
        if trace_id == "0" * 32 or span_id == "0" * 16:
            return None
        return cls(trace_id, span_id, int(flags_hex, 16))

    def __repr__(self) -> str:
        """Compact repr showing the encoded traceparent for log readability."""
        return f"SpanContext({self.encode_w3c_traceparent()!r})"


def current_trace_id() -> str | None:
    """Return the active context's W3C traceparent, or ``None`` (R129).

    Mirrors fastrace's ``current_trace_id``: the traceparent of whatever
    :class:`SpanContext` is active in the current :mod:`contextvars`
    scope, or ``None`` when no context is active. ``None`` is the signal
    the ``http_client`` leaf will use to *not* inject a traceparent header
    (an unconfigured process leaves requests untouched).
    """
    ctx = _current_span_context.get()
    if ctx is None:
        return None
    return ctx.encode_w3c_traceparent()


def local_or_random_span_ctx() -> SpanContext:
    """Return the active context, or a fresh random one if none (R129).

    Mirrors fastrace's ``local_or_random_span_ctx``: useful when a caller
    needs *a* context to emit (e.g. as the parent of a new outbound
    request) and is happy to mint one if the process is unconfigured. The
    minted context is *not* installed as current — call
    :func:`enter_span_with_traceparent` for that.
    """
    return _current_span_context.get() or SpanContext.random()


def enter_span_with_traceparent(
    name: str,
    traceparent: str,
) -> _TraceparentSpan:
    """Return a context manager making ``traceparent`` the active context.

    Mirrors fastrace's ``enter_span_with_traceparent`` as a Python context
    manager (the Rust original returns a fastrace ``Span``, a lifecycle
    object this landing does not model — see the module docstring's
    fastrace::Span downgrade note). ``__enter__`` decodes the traceparent
    via :meth:`SpanContext.decode_w3c_traceparent`: if it is well-formed,
    the decoded :class:`SpanContext` is installed as current for the block
    and returned; if it is malformed, the current context is left
    untouched (the local-parent fallback) and ``None`` is returned. On
    exit the previous context is restored via the saved
    :class:`~contextvars.Token`.

    ``name`` is accepted for signature parity with the Rust original
    (which uses it as the fastrace span name). This landing does not model
    a span name lifecycle, so it is recorded on the context manager for
    debugging but does not affect propagation — a recorded downgrade, not
    a silent drop.
    """
    return _TraceparentSpan(name, traceparent)


class _TraceparentSpan:
    """Context manager behind :func:`enter_span_with_traceparent` (R129).

    Not exported directly; callers obtain one via
    :func:`enter_span_with_traceparent`. See that function's docstring for
    the decode/fallback contract and the module docstring for why this is
    a context manager rather than a fastrace ``Span``.
    """

    __slots__ = ("_name", "_traceparent", "_token")

    def __init__(self, name: str, traceparent: str) -> None:
        self._name = name
        self._traceparent = traceparent
        self._token: contextvars.Token[SpanContext | None] | None = None

    def __enter__(self) -> SpanContext | None:
        """Decode the traceparent and install it as current; None on malformed.

        Returns the decoded :class:`SpanContext` (now active for the block)
        or ``None`` if the traceparent did not parse — in the ``None`` case
        no token is set and the block runs under whatever context was
        active before (the local-parent fallback).
        """
        ctx = SpanContext.decode_w3c_traceparent(self._traceparent)
        if ctx is None:
            return None
        self._token = _current_span_context.set(ctx)
        return ctx

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Restore the previous context if one was installed.

        A ``None`` token (malformed traceparent, no ``__enter__`` install)
        is a no-op. Returning ``None`` never suppresses a block exception.
        :meth:`contextvars.ContextVar.reset` with a stale token would raise
        :class:`ValueError`; the ``None`` guard avoids it.
        """
        if self._token is not None:
            _current_span_context.reset(self._token)
            self._token = None
