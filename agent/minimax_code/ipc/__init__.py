"""JSON-RPC 2.0 over stdio bridge.

Exposes a single class, :class:`IPCServer`, that reads line-delimited
JSON requests from stdin, dispatches them to registered handlers, and
writes responses/notifications/events to stdout.

The wire format is documented in ``docs/ipc-contract.md``:

    - one UTF-8 JSON object per line
    - lines are separated by ``\\n`` (LF)
    - messages are framed in pure JSON-RPC 2.0, with an extra
      convention: when ``result`` / ``error`` are absent we interpret
      the message as a *push* event (with ``event`` and ``data`` fields)
"""

from __future__ import annotations
