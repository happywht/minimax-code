"""Pure-data file-change event types (R16).

Port of grok-build's ``xai-fsnotify`` wire contract (``FsEvent`` /
``FsEventKind``) into MiniMax. The semantics differ in one important way:
grok's source is an OS file watcher that observes *external* editors; ours
is the agent's own tool calls (``write_file`` / ``edit_file``), which are
already atomic and self-attributed. So we keep the pure-data event shape
and the single-causal-stream idea, but drop the debounce windows and the
git lock state machine — there is no OS event storm to settle.

Pure data: no I/O, no asyncio, no intra-package deps. Safe to lift into a
sibling ``-types`` module if a future consumer needs the contract without
the bus.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FsEventKind(StrEnum):
    """What happened to the path(s) in an :class:`FsEvent`.

    Identity-mapped to grok's ``FsEventKind`` at the workspace boundary.
    ``MODIFIED`` is the default for any in-place byte change.
    """

    CREATED = "created"
    MODIFIED = "modified"
    REMOVED = "removed"
    RENAMED = "renamed"


@dataclass(frozen=True)
class FsEvent:
    """One semantic file-change event on the causal stream.

    All ``paths`` share the same ``kind`` within one event (a rename is a
    single event carrying ``(old, new)``). ``seq`` is the bus-assigned
    monotonic counter — the *causal* order consumers should rely on, since
    wall-clock can jitter. ``cause`` attributes the event to the tool or
    handler that produced it (``write_file``, ``edit_file``, ...).
    """

    kind: FsEventKind
    paths: tuple[str, ...]
    cause: str
    seq: int
    monotonic_ns: int
    session_id: str | None = None
    tool_call_id: str | None = None
    # Sorted (key, value) pairs — frozen dataclass needs hashable members.
    attributes: tuple[tuple[str, str], ...] = ()

    def attribute(self, key: str, default: str | None = None) -> str | None:
        """Read a single attribute; O(n) but n is tiny."""
        for k, v in self.attributes:
            if k == key:
                return v
        return default


__all__ = ["FsEvent", "FsEventKind"]
