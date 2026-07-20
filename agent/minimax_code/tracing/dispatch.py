"""Detect whether a logging consumer is wired (R128).

Fusion of grok-build's ``xai-tracing/src/dispatch.rs``. One symbol lands:
:func:`dispatcher_active` — returns ``True`` when the root logger has at
least one real handler (i.e. an observability backend will actually
receive records). It is the gate the request-span factories
(:mod:`~minimax_code.tracing.http_client`, landing later) check before
building a span / minting a traceparent, so an unconfigured process pays
no trace-construction cost.

tracing::dispatcher::get_default -> root logger handlers
--------------------------------------------------------

Rust's :func:`tracing::dispatcher::get_default` answers "is the current
dispatcher a real subscriber, or the :class:`NoSubscriber` sentinel?".
The current dispatcher is whichever a thread has installed via
``with_default`` / ``set_default`` (thread-scoped), falling back to the
global default. The Python landing reads
:data:`logging.getLogger().handlers` instead: a real handler on the
*root* logger is the "a consumer is wired" signal.
:class:`logging.NullHandler` is the faithful counterpart of Rust's
:class:`NoSubscriber` — libraries attach one to say "I emit via logging
but I do not configure output", so it is excluded from the "active"
check exactly as ``NoSubscriber`` is.

Two semantic downgrades are taken and recorded here, not silently:

* **Scope.** Rust's dispatcher can be thread-scoped
  (``with_default``) *and* global. Python's :mod:`logging` has no
  thread-scoped handler concept — handlers live on logger objects,
  which are process-global. So :func:`dispatcher_active` answers only
  the global question. The Rust thread-scoped test
  (``with_default(registry(), ...)`` active inside, inactive outside)
  has no direct Python mirror; the closest analogue — a context
  manager that temporarily swaps the root handler list — is left to
  call sites, not encoded here.

* **Motive.** In Rust the gate exists primarily to suppress *log spam*:
  ``tracing``'s ``log`` compatibility feature downgrades an unconsumed
  span into a ``log`` record at the span's level, which floods
  log-only processes (integration tests, fastrace-only binaries) with
  noise. Python's :mod:`logging` has no such feature — a record with
  no handler is simply dropped — so there is no spam to suppress. The
  surviving Python motive is the other half of the Rust one: skip the
  *construction* cost (trace-id minting, attribute gathering, W3C
  traceparent formatting) when no one will consume the result.

Wiring in MiniMax Code
----------------------

:func:`minimax_code.logging_setup.configure_logging` installs one
:class:`logging.StreamHandler` (stderr) on the root logger, so
:func:`dispatcher_active` is ``True`` in any process that has called it
(production, the HTTP agent, integration tests). It is ``False`` only in
a bare interpreter or a unit test that has isolated the root handlers.
It deliberately does *not* inspect the TelemetryEngine — that is a
separate structured-event bus, not a span/record consumer, and folding
it in would blur the 1:1 ``tracing``-to-:mod:`logging` mapping.
"""

from __future__ import annotations

import logging

__all__ = ["dispatcher_active"]


def dispatcher_active() -> bool:
    """Return ``True`` when the root logger has a real handler wired (R128).

    Mirrors Rust's ``dispatcher_active``: ``True`` means an observability
    backend will actually receive records, ``False`` means nothing is
    consuming them and trace-construction work would be wasted. The check
    is *dynamic* — it re-reads the root logger's handler list every call,
    so wiring a handler (or removing one) takes effect immediately, with
    no caching.

    A :class:`logging.NullHandler` does **not** count as "active": it is
    the :mod:`logging` idiom for "I use logging but configure no output",
    the counterpart of Rust's :class:`NoSubscriber` sentinel. Only a
    non-:class:`~logging.NullHandler` handler on the *root* logger makes
    this return ``True``; handlers attached to a named child logger are
    not inspected (this answers the global question only — see the
    module docstring's scope note). The root logger's *level* is
    irrelevant: this is a consumer-presence check, not a level check.
    """
    root = logging.getLogger()
    return any(
        not isinstance(handler, logging.NullHandler) for handler in root.handlers
    )
