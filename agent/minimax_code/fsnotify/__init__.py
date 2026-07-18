"""Filesystem change bus (R16) — single causal stream of pure-data events.

Fuses grok-build's ``xai-fsnotify`` design (typed FsEvents on one broadcast
channel) into MiniMax's asyncio architecture. The source is our own tool
calls (``write_file`` / ``edit_file``), not an OS watcher, so the stream is
atomic and self-attributed — no debounce windows, no git lock state machine.

Public surface
--------------
* :class:`FsEventBus` — the bus (emit → recent + subscriber fan-out).
* :class:`FsEvent`, :class:`FsEventKind` — pure-data event model.

Wiring lives in :mod:`minimax_code.app` via ``ensure_fs_bus()``; the write
tools emit after a successful write.
"""

from __future__ import annotations

from .bus import FsEventBus
from .events import FsEvent, FsEventKind

__all__ = [
    "FsEvent",
    "FsEventBus",
    "FsEventKind",
]
