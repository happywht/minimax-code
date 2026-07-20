"""MCP transport abstraction (R180).

Fusion of grok-build's ``xai-computer-hub-mcp-adapter/src/transport.rs``
(39 lines) -- the mcp_adapter crate's 2nd leaf. :class:`McpTransport`
defines the async interface consumed by :class:`~minimax_code.mcp_adapter
.bridge.McpBridge` (R181+); concrete implementations (stdio, HTTP+SSE) live
outside this crate, and the trait boundary keeps the bridge testable with
in-memory mocks (mirrors the Rust doccomment).

Python-specific adaptations (no behavior change)
------------------------------------------------

* Rust ``#[async_trait] pub trait McpTransport: Send + Sync`` ->
  :class:`McpTransport` as an :mod:`abc.ABC` with four ``async``
  :func:`abc.abstractmethod` definitions. ``async def`` natively returns a
  coroutine -- the Python equivalent of the ``Pin<Box<dyn Future + Send>>``
  the ``async_trait`` macro desugars to -- so no boxed return type is
  expressed.
* Rust ``Send + Sync`` supertrait bounds -> omitted. asyncio is
  single-threaded under the GIL, so there is no Rust-style thread-safety
  vocabulary to express; a transport is owned and driven by one event loop.
* Rust ``&self`` -> ``self``; ``&str`` name -> ``str``.
* Rust ``serde_json::Value`` arguments -> ``Any`` (consistent with
  :mod:`minimax_code.mcp_adapter.types`).
* Rust ``Result<T, McpError>`` return type -> the success type ``T`` as the
  return annotation; failures are raised as a
  :class:`~minimax_code.mcp_adapter.types.McpError` subclass (the Python
  idiom -- errors propagate via ``raise``, not a ``Result`` envelope). The
  four :class:`~minimax_code.mcp_adapter.types.McpError` subclasses landed
  in R179 are the raise targets.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from minimax_code.mcp_adapter.types import (
    McpCallResult,
    McpServerInfo,
    McpToolDefinition,
)

__all__ = ["McpTransport"]


class McpTransport(ABC):
    """Async interface to a single MCP server connection.

    Implementations manage the underlying JSON-RPC framing (stdio pipe,
    HTTP+SSE stream, etc.) and expose the four lifecycle operations the
    bridge needs. Mirrors ``McpTransport`` in ``transport.rs`` -- a pure
    trait (abstract base); concrete transports are provided by downstream
    consumers.

    All four methods are coroutines (``async def``); failures raise a
    :class:`~minimax_code.mcp_adapter.types.McpError` subclass -- the Python
    equivalent of the Rust ``Result<_, McpError>`` return, where errors
    propagate via ``raise`` rather than a result envelope.
    """

    @abstractmethod
    async def initialize(self) -> McpServerInfo:
        """Perform the MCP ``initialize`` handshake with the server.

        Must be called exactly once before any other method. Returns the
        server's advertised name, version, and capabilities.

        Raises:
            minimax_code.mcp_adapter.types.McpError: on handshake failure.
        """

    @abstractmethod
    async def list_tools(self) -> list[McpToolDefinition]:
        """Discover available tools via MCP ``tools/list``.

        Raises:
            minimax_code.mcp_adapter.types.McpError: on listing failure.
        """

    @abstractmethod
    async def call_tool(self, name: str, arguments: Any) -> McpCallResult:
        """Invoke a tool via MCP ``tools/call``.

        ``arguments`` is the JSON object the model produced for the tool's
        input schema.

        Raises:
            minimax_code.mcp_adapter.types.McpError: on call failure.
        """

    @abstractmethod
    async def close(self) -> None:
        """Gracefully shut down the transport (close pipes, drop connections).

        Implementations must be idempotent -- a second call after a
        successful close must succeed without error (mirrors the Rust
        ``Ok(())`` contract).

        Raises:
            minimax_code.mcp_adapter.types.McpError: on close failure.
        """
