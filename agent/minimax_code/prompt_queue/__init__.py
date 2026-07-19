"""Prompt queue — fusion of grok's ``xai-prompt-queue`` (R40).

The wire-type contract for a queue of not-yet-running user prompts. Where
MiniMax Code drains user input as it arrives (no visible queue), this package is
the vocabulary for a "see / edit / reorder pending prompts" interaction: each
entry carries a stable id, a monotonic version, and client attribution.

Scope of this package
---------------------

* :mod:`.types` (R40) — :class:`~.types.QueueEntryMeta` (actor-internal frozen
  value type), :class:`~.types.QueueEntryWire` and :class:`~.types.QueueChanged`
  (the JSON-RPC wire row and broadcast payload; pydantic models with camelCase
  aliases, defaults, and None-exclusion). Pure data contract; no serialization
  surprises — defaults, None exclusion, and unknown-field tolerance are all
  pinned by tests.

What is NOT here (wiring round): the session actor that owns the queue (grok
keeps it in tokio actor state) and the shell/pager consumers of the broadcast
are platform stacks. A future wiring round feeds these types into the agent
conversation loop and a front-end queue panel; the contract here is what both
sides speak unchanged.
"""

from __future__ import annotations

from .types import QueueChanged, QueueEntryMeta, QueueEntryWire

__all__ = [
    "QueueEntryMeta",
    "QueueEntryWire",
    "QueueChanged",
]
