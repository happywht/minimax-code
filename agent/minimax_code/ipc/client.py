"""In-process client used by tests.

The Tauri↔Python bridge is a stdio channel, so a real-world client
opens a subprocess and reads/writes its pipes. The :class:`IPCClient`
here is purely a test helper — it instantiates a server and a pipe
pair, then drives them with a hand-rolled reader.

We do this by monkey-patching ``IPCServer._handle_line`` so a test
can feed messages without spinning up the executor-based read loop.
"""

from __future__ import annotations

import asyncio
import io
import json
from typing import Any, Optional

from ..config import Config
from ..app import register_app_handlers
from .protocol import Request
from .server import IPCServer


class IPCClient:
    """Synchronous-feel client that drives an :class:`IPCServer` in-process.

    The test API is intentionally tiny: :meth:`request` and
    :meth:`collect_events`.
    """

    def __init__(self) -> None:
        self._input = io.StringIO()
        self._output = io.StringIO()
        self.server = IPCServer(
            config=Config.from_env(),
            stdin=self._input,
            stdout=self._output,
        )
        # Wire up the same handlers the real `__main__` registers.
        register_app_handlers(self.server)
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        # Wrap the server's writer to also fan out events to our queue.
        original_send = self.server._send

        async def patched(payload: bytes) -> None:
            await original_send(payload)
            # Parse the last emitted line.
            lines = self._output.getvalue().splitlines()
            if not lines:
                return
            try:
                obj = json.loads(lines[-1])
            except json.JSONDecodeError:
                return
            if "event" in obj:
                await self._events.put(obj)

        self.server._send = patched  # type: ignore[assignment]

    async def request(self, method: str, params: Any = None, *, timeout: float = 5.0) -> Any:
        """Send a request and await its response."""
        req_id = f"test-{method}-{asyncio.get_event_loop().time()}"
        req = Request(id=req_id, method=method, params=params)
        line = req.to_line()
        # Drive the dispatcher directly.
        await self.server._handle_line(line + "\n")
        # Read the response that was emitted.
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            # Search the accumulated output for a response with our id.
            for raw in self._output.getvalue().splitlines():
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if obj.get("id") == req_id:
                    if "error" in obj:
                        raise RuntimeError(obj["error"])
                    return obj.get("result")
            await asyncio.sleep(0.01)
        raise asyncio.TimeoutError(f"no response for {method!r} within {timeout}s")

    async def collect_events(self, n: int, *, timeout: float = 5.0) -> list[dict[str, Any]]:
        """Block until ``n`` events have been emitted, or timeout."""
        out: list[dict[str, Any]] = []
        deadline = asyncio.get_event_loop().time() + timeout
        while len(out) < n and asyncio.get_event_loop().time() < deadline:
            try:
                evt = await asyncio.wait_for(self._events.get(), timeout=0.5)
                out.append(evt)
            except asyncio.TimeoutError:
                continue
        return out


__all__ = ["IPCClient"]
