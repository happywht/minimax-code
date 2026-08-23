"""v1.2.2 regression tests — concurrent permission gaters.

The bug: ``agent.send_message`` stashed its request-scoped
:class:`~minimax_code.perm_consent.PermissionGater` on the single
``server._permission_gater`` attribute. When two runs overlapped
(e.g. two sessions with an ``ask`` rule each), run B's registration
overwrote run A's gater — A's ``permission.request`` prompts could
never be resolved and were denied by the 5-minute timeout.

The fix: a session-keyed registry (``server._permission_gaters``) that
``permission.resolve`` walks until a gater claims the request_id. The
legacy single-slot attribute is still honoured (several tests set it
directly) and still written (backwards compatibility for readers).
"""

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace
from typing import Any

import pytest

from minimax_code.config import Config
from minimax_code.ipc.handlers_permissions import register_permission_handlers
from minimax_code.ipc.server import IPCServer
from minimax_code.perm_consent import (
    PermissionGater,
    register_gater,
    resolve_any_gater,
    unregister_gater,
)


async def _noop_emit(event: str, data: Any) -> None:  # pragma: no cover — unused
    pass

# Parked request_consent tasks — cancelled by the autouse fixture so a
# still-waiting prompt doesn't leak a pending task into loop teardown.
_PARKED: list[asyncio.Task[bool]] = []


async def _park(gater: PermissionGater, *, tool: str = "exec_command") -> str:
    """Start a ``request_consent`` call and return once its id is pending."""
    task = asyncio.create_task(gater.request_consent(tool=tool, args={}, timeout=5.0))
    _PARKED.append(task)
    for _ in range(200):
        if gater.pending_ids():
            return gater.pending_ids()[0]
        await asyncio.sleep(0.005)
    task.cancel()
    raise AssertionError("request_consent never became pending")


@pytest.fixture(autouse=True)
def _cleanup_parked_tasks() -> Any:
    yield
    for task in _PARKED:
        if not task.done():
            task.cancel()
    _PARKED.clear()


# ---------------------------------------------------------------------------
# Registry semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_keeps_concurrent_gaters_resolvable() -> None:
    """The core regression: run B registering must not orphan run A's
    pending consent request (the old single-slot overwrite)."""
    server = SimpleNamespace()
    gater_a = PermissionGater(emit=_noop_emit)
    gater_b = PermissionGater(emit=_noop_emit)

    register_gater(server, "ses_a", gater_a)
    rid_a = await _park(gater_a)
    # Run B starts later — the legacy slot now points at gater_b.
    register_gater(server, "ses_b", gater_b)
    assert server._permission_gater is gater_b, "legacy slot semantics changed"

    # Run A's prompt must still be claimable.
    assert resolve_any_gater(server, rid_a, True) is True


@pytest.mark.asyncio
async def test_concurrent_gaters_resolve_their_own_requests() -> None:
    """Each resolve lands on the owning gater only — no cross-talk."""
    server = SimpleNamespace()
    gater_a = PermissionGater(emit=_noop_emit)
    gater_b = PermissionGater(emit=_noop_emit)
    register_gater(server, "ses_a", gater_a)
    register_gater(server, "ses_b", gater_b)

    rid_a = await _park(gater_a)
    rid_b = await _park(gater_b)

    assert resolve_any_gater(server, rid_a, True) is True
    assert gater_a.resolve(rid_a, False) is True  # already done → still claims
    # B untouched by A's resolve.
    assert gater_b.has_pending(rid_b)
    assert resolve_any_gater(server, rid_b, False) is True


@pytest.mark.asyncio
async def test_legacy_single_slot_still_resolves() -> None:
    """Tests (and any older reader) that only set ``_permission_gater``
    keep working — the fallback path inside ``resolve_any_gater``."""
    server = SimpleNamespace()
    gater = PermissionGater(emit=_noop_emit)
    server._permission_gater = gater  # legacy direct assignment, no registry

    rid = await _park(gater)
    assert resolve_any_gater(server, rid, True) is True


@pytest.mark.asyncio
async def test_unregister_removes_gater() -> None:
    """A finished run's gater must stop claiming resolves."""
    server = SimpleNamespace()
    gater = PermissionGater(emit=_noop_emit)
    register_gater(server, "ses_done", gater)
    rid = await _park(gater)

    unregister_gater(server, "ses_done")
    # Neither the registry nor the stale legacy slot may claim it.
    assert resolve_any_gater(server, rid, True) is False
    assert server._permission_gater is None

    # Idempotent + other runs unaffected.
    unregister_gater(server, "ses_done")
    other = PermissionGater(emit=_noop_emit)
    register_gater(server, "ses_other", other)
    rid_other = await _park(other)
    assert resolve_any_gater(server, rid_other, True) is True


@pytest.mark.asyncio
async def test_resolve_unknown_id_returns_false() -> None:
    server = SimpleNamespace()
    gater = PermissionGater(emit=_noop_emit)
    register_gater(server, "ses_x", gater)
    assert resolve_any_gater(server, "perm_does_not_exist", True) is False


# ---------------------------------------------------------------------------
# Handler-level wiring
# ---------------------------------------------------------------------------


class _FakeContext:
    def __init__(self, server: IPCServer) -> None:
        self.server = server
        self.reply_value: dict[str, Any] | None = None
        self.error_value: dict[str, Any] | None = None

    async def reply(self, value: Any) -> None:
        self.reply_value = value

    async def reply_error(self, code: int, message: str, data: Any = None) -> None:
        self.error_value = {"code": code, "message": message, "data": data}

    async def emit(self, event: str, data: Any) -> None:  # pragma: no cover
        pass


@pytest.mark.asyncio
async def test_permission_resolve_handler_walks_registry() -> None:
    """End-to-end: ``permission.resolve`` resolves run A's prompt even
    though run B registered afterwards (old code replied 'unknown
    request_id' here)."""
    server = IPCServer(
        config=Config.from_env(),
        stdin=io.StringIO(),
        stdout=io.StringIO(),
    )
    register_permission_handlers(server)
    handlers = server._handlers

    gater_a = PermissionGater(emit=_noop_emit)
    gater_b = PermissionGater(emit=_noop_emit)
    register_gater(server, "ses_a", gater_a)
    rid_a = await _park(gater_a)
    register_gater(server, "ses_b", gater_b)  # overwrites the legacy slot

    ctx = _FakeContext(server)
    await handlers["permission.resolve"](
        {"request_id": rid_a, "decision": "allow"}, ctx
    )

    assert ctx.error_value is None, ctx.error_value
    assert ctx.reply_value is not None
    assert ctx.reply_value["ok"] is True
    assert ctx.reply_value["request_id"] == rid_a

    unregister_gater(server, "ses_a")
    unregister_gater(server, "ses_b")
