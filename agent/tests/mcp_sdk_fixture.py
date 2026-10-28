"""Real official-SDK server on an ephemeral loopback port; no model/service IO."""

from __future__ import annotations

import asyncio
import socket
from contextlib import asynccontextmanager, nullcontext

import uvicorn
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent


class LocalServer(uvicorn.Server):
    def capture_signals(self):
        return nullcontext()


@asynccontextmanager
async def loopback(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    sock.setblocking(False)
    server = LocalServer(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            log_config=None,
            access_log=False,
            lifespan="on",
            timeout_graceful_shutdown=2,
        )
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    task.result()
                    raise RuntimeError("loopback fixture failed to start")
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        sock.close()


def official_server():
    server = MCPServer("offline-sdk-fixture")
    entered = asyncio.Event()
    release = asyncio.Event()

    @server.tool()
    def add(a: int, b: int) -> int:
        return a + b

    @server.tool()
    def structured(value: object) -> CallToolResult:
        return CallToolResult(
            content=[TextContent(type="text", text="retained")], structured_content=value
        )

    @server.tool()
    def tool_error() -> CallToolResult:
        return CallToolResult(
            content=[TextContent(type="text", text="fixture error")], is_error=True
        )

    @server.tool()
    async def delayed() -> str:
        entered.set()
        await release.wait()
        return "finished"

    return server, entered, release
