"""Offline stdio process fixture, launched only through managed execution."""

import asyncio
import os
import sys

from mcp.server import MCPServer
from mcp.types import CallToolResult

server = MCPServer("controlled-stdio")


@server.tool()
def add(a: int, b: int) -> int:
    return a + b


@server.tool()
def environment(include_argv: bool = False) -> CallToolResult:
    if include_argv:
        return CallToolResult(content=[], structured_content={"argv": sys.argv[1:]})
    return CallToolResult(
        content=[],
        structured_content={
            name: os.environ.get(name) for name in ("MCP_TEST_AMBIENT_SECRET", "MCP_TEST_EXPLICIT")
        },
    )


if __name__ == "__main__":
    asyncio.run(server.run_stdio_async())
