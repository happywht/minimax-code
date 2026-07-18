"""Mid-turn user interjection buffer (R24).

Ports grok-build's ``xai-interjection-core`` crate — the mechanism that
lets a user inject a message into an in-flight agent turn without
tearing down the conversation. The buffer collects out-of-band
interjections as they arrive; the agent drains them at a *safe point*
(between tool turns, never mid-call), framing each as a synthetic
``user`` message the model sees on its next loop iteration.

Three pieces, mirroring the Rust crate's three modules:

* :class:`EventQueue` — a generic ``Arc<Mutex<Vec<E>>>``-style shared FIFO
  queue (clones share one queue; ``push`` / ``push_capped`` /
  ``drain_matching`` / ``drain_all`` / ``snapshot``). Pure logic,
  internally synchronised.
* :func:`format_interjection` + :func:`user_query` +
  :data:`LARGE_PROMPT_THRESHOLD` — wrap raw text as the canonical
  ``<user_query>`` envelope with a mid-turn note; truncate over-long
  input to protect the token budget.
* :class:`PendingInterjection` / :class:`FormattedInterjection` +
  :func:`drain_formatted` — the buffer of pending interjections and the
  one-shot "drain + frame every entry as its own user message (never
  merged)" helper, with a host-supplied ``sanitize_text`` hook.

Why ``threading.Lock`` (not ``asyncio.Lock``)
--------------------------------------------

The queue is pure in-memory list shuffling with no ``await`` inside, and
producers may be any thread (an IPC handler, a watchdog). A synchronous
lock keeps the queue's "clone-shared, lock-protected" contract identical
to grok's ``Arc<Mutex<..>>`` while staying cheap and await-free. grok's
poisoned-mutex recovery (``unwrap_or_else(|e| e.into_inner())``) has no
Python analogue — :class:`threading.Lock` cannot poison.

Truncation adaptation
---------------------

grok truncates at a *byte* boundary (UTF-8 ``char_indices``) because Rust
strings are byte buffers. Python strings are code-point sequences, so
:func:`format_interjection` truncates at the code-point boundary instead
— semantically equivalent (cap oversized input to protect the prompt
budget) and never capable of slicing a multi-byte character in half. The
threshold stays 25_000.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

__all__ = [
    "LARGE_PROMPT_THRESHOLD",
    "EventQueue",
    "FormattedInterjection",
    "InterjectionBuffer",
    "PendingInterjection",
    "drain_formatted",
    "format_interjection",
    "user_query",
]

E = TypeVar("E")
Attachment = TypeVar("Attachment")

#: Truncation threshold, matching grok's ``LARGE_PROMPT_THRESHOLD``.
#: Caps an interjection so a runaway paste can't blow the prompt budget.
LARGE_PROMPT_THRESHOLD: int = 25_000


# ---------------------------------------------------------------------------
# format.rs
# ---------------------------------------------------------------------------


def user_query(user_message: str) -> str:
    """Wrap a user message in the canonical ``<user_query>`` envelope."""
    return f"<user_query>\n{user_message}\n</user_query>"


def format_interjection(text: str) -> str:
    """Wrap interjection text as a synthetic user message with a mid-turn note.

    No deferral instruction: the model decides how to weigh the
    interjection against in-flight work — grok deliberately leaves that
    judgment to the model rather than commanding "drop everything".
    """
    if len(text) > LARGE_PROMPT_THRESHOLD:
        truncated = f"{text[:LARGE_PROMPT_THRESHOLD]}... [truncated]"
    else:
        truncated = text
    return (
        "The user sent a message while you are working:\n"
        + user_query(truncated)
    )


# ---------------------------------------------------------------------------
# events.rs — EventQueue
# ---------------------------------------------------------------------------


class _SharedState(Generic[E]):
    """The shared backing store two :class:`EventQueue` clones hold.

    Mirrors grok's ``Arc<Mutex<Vec<E>>>``: the :class:`EventQueue` is a
    thin handle, ``clone()`` returns a new handle over the *same* state,
    so a producer and a consumer can each hold their own queue object
    while operating on one underlying list.
    """

    def __init__(self) -> None:
        self.events: list[E] = []
        self.lock = threading.Lock()


class EventQueue(Generic[E]):
    """Shared FIFO event queue for the push path.

    Producers enqueue out-of-band events; readers drain the ones relevant
    to them at hook points. Clones share one underlying queue (grok's
    ``Arc<Mutex<Vec<E>>>`` contract), so an event pushed through one clone
    is visible to every other. Internally synchronised via a
    :class:`threading.Lock` — operations are fast list shuffles with no
    I/O, so a synchronous lock matches grok's semantics without forcing
    callers to ``await``.
    """

    def __init__(self, state: _SharedState[E] | None = None) -> None:
        self._state = state if state is not None else _SharedState()

    def clone(self) -> EventQueue[E]:
        """Return a new queue handle sharing the same backing state."""
        return EventQueue(self._state)

    def push(self, event: E) -> None:
        """Producer hook: record an event for later draining."""
        with self._state.lock:
            self._state.events.append(event)

    def push_capped(self, event: E, max_size: int) -> None:
        """Push, then drop the oldest events so at most ``max_size`` remain."""
        with self._state.lock:
            events = self._state.events
            events.append(event)
            if len(events) > max_size:
                # Drop the oldest (front) entries — mirrors Rust's
                # ``q.drain(..excess)``.
                del events[: len(events) - max_size]

    def __len__(self) -> int:
        with self._state.lock:
            return len(self._state.events)

    def is_empty(self) -> bool:
        return len(self) == 0

    def drain_matching(self, take: Callable[[E], bool]) -> list[E]:
        """Remove and return events matching ``take``, retaining the rest.

        FIFO order is preserved in both the returned and retained sets.
        """
        with self._state.lock:
            events = self._state.events
            self._state.events = []
            matched: list[E] = []
            kept: list[E] = []
            for e in events:
                (matched if take(e) else kept).append(e)
            self._state.events = kept
            return matched

    def drain_all(self) -> list[E]:
        """Remove and return all events, leaving the queue empty (FIFO order)."""
        with self._state.lock:
            taken = self._state.events
            self._state.events = []
            return taken

    def clear(self) -> None:
        """Discard all events."""
        with self._state.lock:
            self._state.events.clear()

    def snapshot(self) -> list[E]:
        """A shallow copy of the current events, for inspection without draining.

        Mirrors grok's ``snapshot`` (which requires ``E: Clone`` in Rust);
        Python objects are reference types, so a shallow list copy is the
        faithful analogue — callers see the entries as they are but the
        queue still owns them.
        """
        with self._state.lock:
            return list(self._state.events)


# ---------------------------------------------------------------------------
# buffer.rs — InterjectionBuffer + drain_formatted
# ---------------------------------------------------------------------------


@dataclass
class PendingInterjection(Generic[Attachment]):
    """A buffered mid-turn interjection awaiting the next safe drain point.

    ``attachments`` is host-defined (inline images, asset IDs); the core
    never reads it — it flows straight through to the drained
    :class:`FormattedInterjection`.
    """

    text: str
    attachments: list[Attachment] = field(default_factory=list)


@dataclass
class FormattedInterjection(Generic[Attachment]):
    """A drained entry, wrapped and ready to emit as a synthetic user message."""

    text: str
    attachments: list[Attachment] = field(default_factory=list)


#: Type alias matching grok's ``InterjectionBuffer<Attachment>`` — an
#: :class:`EventQueue` of :class:`PendingInterjection`. The ``Attachment``
#: parameter is whatever the caller puts in ``PendingInterjection.attachments``;
#: Python's duck typing carries it, so no rigid generic-over-generic alias is
#: needed.
InterjectionBuffer = EventQueue[PendingInterjection]


def drain_formatted(
    buffer: EventQueue[PendingInterjection],
    sanitize_text: Callable[[str], str],
) -> list[FormattedInterjection]:
    """Drain ``buffer``, framing each entry as a synthetic user message.

    FIFO, one message per entry, **never merged**. ``sanitize_text`` runs
    on the raw text *first* (hosts strip artifacts like image-placeholder
    paths); pass ``lambda s: s`` if none. Returns ``[]`` when the buffer
    is empty.
    """
    drained: list[FormattedInterjection] = []
    for entry in buffer.drain_all():
        drained.append(
            FormattedInterjection(
                text=format_interjection(sanitize_text(entry.text)),
                attachments=entry.attachments,
            )
        )
    return drained
