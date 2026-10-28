"""Both signal registration APIs trigger and await HTTP runtime shutdown."""
from __future__ import annotations

import asyncio
import signal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import uvicorn

from minimax_code import __main__ as entry
from minimax_code import app, http_server
from minimax_code.config import Config
from minimax_code.runtime import shutdown


@pytest.mark.parametrize("fallback", [False, True], ids=["asyncio", "signal-fallback"])
async def test_sigterm_callback_requests_exit_and_awaits_shutdown(monkeypatch, fallback):
    callbacks = []
    order = []
    server = SimpleNamespace()

    def register_loop(signum, callback, *args):
        assert signum == signal.SIGTERM
        if fallback:
            raise NotImplementedError
        callbacks.append((callback, args))

    def register_signal(signum, callback):
        if signum != signal.SIGTERM:
            return signal.getsignal(signum)
        assert fallback
        callbacks.append((callback, (signum, None)))

    class HTTPServer:
        should_exit = False

        async def serve(self):
            assert len(callbacks) == 1
            callback, args = callbacks[0]
            # asyncio invokes callback(*registered_args); signal.signal
            # supplies signum/frame itself. Neither may raise TypeError.
            callback(*args)
            assert self.should_exit is True
            order.append("serve-returned")

    async def close_runtime(actual_server):
        assert actual_server is server
        await asyncio.sleep(0)
        order.append("shutdown-awaited")

    monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", register_loop)
    monkeypatch.setattr(signal, "signal", register_signal)
    monkeypatch.setattr(app, "init_runtime", AsyncMock())
    monkeypatch.setattr(app, "initialize_execution_service", AsyncMock())
    monkeypatch.setattr(entry, "IPCServer", lambda **kwargs: server)
    monkeypatch.setattr(entry, "register_app_handlers", lambda actual_server: None)
    monkeypatch.setattr(http_server, "build_app", lambda actual_server: object())
    monkeypatch.setattr(uvicorn, "Config", lambda *args, **kwargs: object())
    monkeypatch.setattr(uvicorn, "Server", lambda config: HTTPServer())
    monkeypatch.setattr(shutdown, "shutdown_runtime", AsyncMock(side_effect=close_runtime))

    assert await entry.amain(Config(), "http", 18776, "127.0.0.1") == 0
    assert order == ["serve-returned", "shutdown-awaited"]
    shutdown.shutdown_runtime.assert_awaited_once_with(server)
