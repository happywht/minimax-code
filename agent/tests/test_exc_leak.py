"""Tests for P2#21: verify that error messages sent to the client
do NOT include raw exception details (str(exc)).

We check both:
1. Static analysis — no {exc} / {exc!r} in reply_error / HandlerError messages
2. Runtime — server.dispatch returns generic "internal error" on unhandled exceptions
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 1. Static analysis: grep handler source files for leaked {exc}
# ---------------------------------------------------------------------------

IPC_DIR = Path(__file__).resolve().parent.parent / "minimax_code" / "ipc"

# Files that contain handler logic (reply_error / HandlerError calls)
HANDLER_FILES = sorted(IPC_DIR.glob("handlers_*.py")) + [
    IPC_DIR / "builtins.py",
]

# Pattern: f"... {exc}" or f"... {exc!r}" in reply_error or HandlerError calls
LEAK_PATTERN = re.compile(
    r'(?:reply_error|HandlerError).*?f"[^"]*\{exc!r?\}"',
)

# Exceptions: these are user-input validation, not internal leak
ALLOWED_PATTERNS = [
    re.compile(r'"invalid'),       # "invalid JSON", "invalid progress"
    re.compile(r'"unknown job'),   # "unknown job_id: {job_id!r}"
    re.compile(r'"could not compute'),  # cron validation
    re.compile(r"'[^']*' not found"),   # notification/workflow not found
]


@pytest.mark.parametrize("filepath", HANDLER_FILES, ids=lambda p: p.name)
def test_no_exc_leak_in_reply_error(filepath: Path) -> None:
    """Every handler file must not pass raw {exc} to reply_error / HandlerError."""
    if not filepath.exists():
        pytest.skip(f"{filepath.name} not found")
    source = filepath.read_text(encoding="utf-8")
    for i, line in enumerate(source.splitlines(), 1):
        if not LEAK_PATTERN.search(line):
            continue
        # Check if it's an allowed pattern
        is_allowed = any(p.search(line) for p in ALLOWED_PATTERNS)
        if is_allowed:
            continue
        pytest.fail(
            f"{filepath.name}:{i}: leaked {{exc}} in error message:\n  {line.strip()}"
        )


def test_no_exc_leak_in_server_dispatch() -> None:
    """server.py dispatch must send generic 'internal error' without details."""
    server_path = IPC_DIR / "server.py"
    source = server_path.read_text(encoding="utf-8")
    # Count occurrences of the sanitized version
    sanitized = source.count('message="internal error"')
    # Should have at least 2 (stdio dispatch + HTTP handle_request)
    assert sanitized >= 2, (
        f"Expected >= 2 sanitized 'internal error' messages, found {sanitized}"
    )
    # Should NOT have the old f-string version
    leaked = source.count('message=f"internal error: {exc}"')
    assert leaked == 0, (
        f"Found {leaked} leaked {{exc}} in server.py internal error messages"
    )


def test_no_exc_leak_in_http_server() -> None:
    """http_server.py must not leak internal details in generic errors."""
    http_path = IPC_DIR.parent / "http_server.py"
    source = http_path.read_text(encoding="utf-8")
    # The "request body could not be read" message should be sanitized
    assert 'message="request body could not be read"' in source
    assert 'f"request body could not be read: {exc}"' not in source
    # "invalid JSON" is user-facing, allowed to include JSON parse error
    # but the generic body-read error must be sanitized


# ---------------------------------------------------------------------------
# 2. Runtime: verify server.dispatch returns generic message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_server_dispatch_sanitizes_exception() -> None:
    """When a handler raises an unhandled Exception, the dispatch
    layer must return 'internal error' without the exception text."""
    from unittest.mock import AsyncMock

    from minimax_code.config import Config
    from minimax_code.ipc.server import IPCServer

    server = IPCServer(config=Config.from_env(), stdin=None, stdout=None)
    server._send = AsyncMock()

    async def _boom(ctx, params):
        raise RuntimeError("SECRET_DATABASE_PASSWORD_LEAKED")

    server.register("test.exc_leak", _boom)

    # Test handle_request (HTTP path)
    result = await server.handle_request({
        "jsonrpc": "2.0",
        "id": "test-1",
        "method": "test.exc_leak",
        "params": {},
    })

    assert result is not None
    error_msg = result["error"]["message"]
    assert error_msg == "internal error", f"Leaked exception: {error_msg}"
    assert "SECRET_DATABASE_PASSWORD" not in error_msg


@pytest.mark.asyncio
async def test_handler_error_still_propagates_code() -> None:
    """HandlerError with custom code/message still works after sanitization."""
    from unittest.mock import AsyncMock

    from minimax_code.config import Config
    from minimax_code.ipc.handler_utils import HandlerError
    from minimax_code.ipc.server import IPCServer

    server = IPCServer(config=Config.from_env(), stdin=None, stdout=None)
    server._send = AsyncMock()

    async def _fail(ctx, params):
        raise HandlerError(-32001, "custom user-facing error")

    server.register("test.handler_error", _fail)

    result = await server.handle_request({
        "jsonrpc": "2.0",
        "id": "test-2",
        "method": "test.handler_error",
        "params": {},
    })

    assert result is not None
    assert result["error"]["code"] == -32001
    assert result["error"]["message"] == "custom user-facing error"


@pytest.mark.asyncio
async def test_handler_logger_called_on_exception() -> None:
    """Verify that logger.exception is called when a handler fails."""
    import logging
    from unittest.mock import AsyncMock, patch

    from minimax_code.config import Config
    from minimax_code.ipc.server import IPCServer

    server = IPCServer(config=Config.from_env(), stdin=None, stdout=None)
    server._send = AsyncMock()

    async def _fail_logged(ctx, params):
        raise ValueError("some internal error")

    server.register("test.logged_exc", _fail_logged)

    with patch.object(
        logging.getLogger("minimax_code.ipc.server"),
        "exception",
    ) as mock_log:
        await server.handle_request({
            "jsonrpc": "2.0",
            "id": "test-3",
            "method": "test.logged_exc",
            "params": {},
        })
        mock_log.assert_called_once()
