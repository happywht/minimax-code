"""Permission consent gater — real user-in-the-loop tool-call approval.

The :class:`PermissionGater` bridges the agent's tool dispatch loop with
the IPC ``permission.request`` event. When a tool's rule action is
``ask`` (and there's no in-flight one-shot consent for the same tool),
the gater:

1. Emits ``permission.request`` carrying a unique ``request_id``, the
   tool name, and the (truncated) tool arguments.
2. Creates an :class:`asyncio.Future` and parks it in
   :attr:`_pending[request_id]`.
3. Awaits the future.

The frontend's permission modal listens for ``permission.request``,
shows the prompt, and POSTs ``permission.resolve { request_id,
decision }``. The ``permission.resolve`` handler calls
:meth:`PermissionGater.resolve`, which sets the future's result and
unblocks the agent loop.

Without the gater, the ``PermissionStore.is_allowed`` default is
``True`` for unconfigured tools, and ``ask`` rules never actually
pause — the loop just runs the tool. This module is the missing
piece that turns "ask" into a real prompt.

Thread / concurrency
--------------------

The gater is designed for a single event loop. The :class:`PendingRequest`
map is updated under an :class:`asyncio.Lock` so concurrent
``request_consent`` calls don't trample each other. ``resolve`` is
synchronous (it just sets a future) so it can be called from any
awaitable, including the IPC handler context that resolves the user
decision.

Timeouts
--------

The default consent timeout is 5 minutes. A timed-out request is
denied (returns ``False``) so the agent loop never hangs on a UI
that crashed. Tests can pass a smaller ``timeout=`` to keep them
snappy.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# Type alias for the emit callable. The IPC layer hands us
# ``ctx.emit`` which serializes to stdout; tests can substitute a
# no-op or in-memory recorder.
EmitFn = Callable[[str, Any], Awaitable[None]]


@dataclass
class PendingRequest:
    """A single in-flight consent request.

    Attributes
    ----------
    request_id:
        Opaque id the frontend echoes back via ``permission.resolve``.
    tool:
        Tool name the agent is about to invoke.
    args:
        Sanitised copy of the tool arguments.
    future:
        :class:`asyncio.Future` the gater awaits; resolved with a
        boolean (True = allow, False = deny).
    """

    request_id: str
    tool: str
    args: dict[str, Any]
    future: asyncio.Future[bool]


class PermissionGater:
    """In-process consent manager.

    The gater is created per-request (one per ``agent.send_message``
    call) and stashed on the server so the ``permission.resolve``
    handler can find it via :func:`getattr(server, "_permission_gater")`.

    The gater is *not* shared between concurrent ``agent.send_message``
    invocations. Each fresh request gets its own gater — the
    request_id-keyed map inside the gater is what makes
    cross-invocation ``resolve`` calls safe (the right future still
    gets set, even if a stale resolve arrives for a different
    request).
    """

    def __init__(self, *, emit: EmitFn) -> None:
        self._emit = emit
        self._pending: dict[str, PendingRequest] = {}
        self._lock = asyncio.Lock()
        # 5 minutes — long enough for the user to think about a
        # destructive command, short enough that a hung UI doesn't
        # tie up the agent loop indefinitely.
        self.default_timeout: float = 300.0

    # -- introspection ------------------------------------------------------

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def has_pending(self, request_id: str) -> bool:
        return request_id in self._pending

    def pending_ids(self) -> list[str]:
        return list(self._pending.keys())

    # -- request flow -------------------------------------------------------

    async def request_consent(
        self,
        *,
        tool: str,
        args: dict[str, Any],
        session_id: str | None = None,
        timeout: float | None = None,
    ) -> bool:
        """Emit a ``permission.request`` event and block until resolve.

        Returns ``True`` if the user allowed, ``False`` if denied
        (or timed out). The pending entry is always cleaned up
        before returning, so a later ``resolve`` for the same
        request_id is a no-op.

        ``session_id`` (v1.2.0) tags the event with the conversation
        the gated tool call belongs to, so the frontend modal can show
        which session is asking — several parallel sessions used to pop
        indistinguishable prompts.
        """
        request_id = f"perm_{uuid.uuid4().hex[:12]}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()

        async with self._lock:
            self._pending[request_id] = PendingRequest(
                request_id=request_id, tool=tool, args=dict(args), future=future
            )

        payload: dict[str, Any] = {
            "request_id": request_id,
            "tool": tool,
            "args": dict(args),
        }
        if session_id:
            payload["session_id"] = session_id

        try:
            await self._emit("permission.request", payload)
        except Exception:  # pragma: no cover — defensive
            logger.exception(
                "emit permission.request failed for tool=%s (denying)", tool
            )
            await self._forget(request_id)
            if not future.done():
                future.set_result(False)
            return False

        try:
            decision = await asyncio.wait_for(
                future, timeout=timeout if timeout is not None else self.default_timeout
            )
            return bool(decision)
        except TimeoutError:
            logger.warning(
                "permission.request %s timed out for tool=%s (denying)",
                request_id,
                tool,
            )
            return False
        finally:
            await self._forget(request_id)

    async def _forget(self, request_id: str) -> None:
        async with self._lock:
            self._pending.pop(request_id, None)

    # -- resolve flow -------------------------------------------------------

    def resolve(self, request_id: str, decision: bool) -> bool:
        """Resolve a pending request.

        Returns ``True`` if a pending future was set, ``False`` if
        the id is unknown (already resolved, expired, or never
        existed). Idempotent.
        """
        pending = self._pending.get(request_id)
        if pending is None:
            return False
        if pending.future.done():
            return True
        pending.future.set_result(bool(decision))
        return True

    def cancel_all(self) -> int:
        """Deny every pending request (used on shutdown / cancel).

        Returns the number of futures that were set.
        """
        n = 0
        for pr in list(self._pending.values()):
            if not pr.future.done():
                pr.future.set_result(False)
                n += 1
        return n


__all__ = ["PermissionGater", "PendingRequest"]
