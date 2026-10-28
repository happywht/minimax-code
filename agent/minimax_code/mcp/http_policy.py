"""Endpoint-scoped HTTP policy for the official SDK (no ambient credentials)."""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

import httpx2

MAX_HTTP_BODY = 2 * 1024 * 1024
_TOKEN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_RESERVED = {
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "content-type",
    "accept",
    "accept-encoding",
    "mcp-protocol-version",
    "mcp-method",
    "upgrade",
}


class MCPHTTPPolicyError(ValueError):
    """Fixed diagnostic; never includes a URL, header value or remote body."""


def validate_url(url: Any) -> str:
    if not isinstance(url, str) or not url or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        raise MCPHTTPPolicyError("MCP URL must be an absolute HTTP(S) URL")
    try:
        parsed = urlsplit(url)
        port = parsed.port
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or port is not None
            and not 0 < port <= 65535
        ):
            raise ValueError
        httpx2.URL(url)
    except (ValueError, httpx2.InvalidURL) as exc:
        raise MCPHTTPPolicyError("MCP URL must be HTTP(S), without userinfo or fragment") from exc
    return url


def validate_headers(headers: Any, bearer_token: Any = None) -> dict[str, str]:
    if headers is None:
        headers = {}
    if not isinstance(headers, dict):
        raise MCPHTTPPolicyError("MCP headers must be a string object")
    result: dict[str, str] = {}
    seen: set[str] = set()
    for key, value in headers.items():
        if not isinstance(key, str) or not _TOKEN.fullmatch(key) or not isinstance(value, str):
            raise MCPHTTPPolicyError("Invalid MCP header")
        lower = key.lower()
        if lower in seen or lower in _RESERVED or lower.startswith("mcp-param-"):
            raise MCPHTTPPolicyError("Duplicate or reserved MCP header")
        if any(ord(c) < 32 or ord(c) >= 127 for c in value):
            raise MCPHTTPPolicyError("Invalid MCP header value")
        seen.add(lower)
        result[key] = value
    if bearer_token is not None:
        if not isinstance(bearer_token, str) or any(
            ord(c) <= 32 or ord(c) >= 127 for c in bearer_token
        ):
            raise MCPHTTPPolicyError("Invalid MCP bearer token")
        if bearer_token:
            if "authorization" in seen:
                raise MCPHTTPPolicyError("Configure bearer token or Authorization, not both")
            result["Authorization"] = f"Bearer {bearer_token}"
    return result


def quiet_sdk_diagnostics() -> None:
    """SDK debug/error logs include wire payloads and URLs; expose only our errors.

    These packages belong to this integration (the project's httpx is separate).
    Parent propagation and already-created child handlers must both be stopped.
    """
    for name in ("mcp", "httpx2", "httpcore2"):
        root = logging.getLogger(name)
        root.handlers[:] = [logging.NullHandler()]
        root.propagate = False
        for child_name, child in tuple(logging.Logger.manager.loggerDict.items()):
            if child_name.startswith(name + ".") and isinstance(child, logging.Logger):
                child.handlers.clear()
                child.propagate = True


class _BoundedBody(httpx2.AsyncByteStream):
    def __init__(self, stream: httpx2.AsyncByteStream) -> None:
        self._stream = stream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        size = 0
        async for chunk in self._stream:
            size += len(chunk)
            if size > MAX_HTTP_BODY:
                raise MCPHTTPPolicyError("MCP HTTP body limit exceeded")
            yield chunk

    async def aclose(self) -> None:
        await self._stream.aclose()


def http_client_factory(url: str, *, streamable_http: bool = False):
    """Endpoint-scoped SDK client factory; transport determines SSE consumer roles."""
    endpoint = httpx2.URL(validate_url(url))
    origin = endpoint.scheme, endpoint.host, endpoint.port

    async def check_request(request: httpx2.Request) -> None:
        target = request.url
        if target.userinfo or (target.scheme, target.host, target.port) != origin:
            raise MCPHTTPPolicyError("MCP request left configured origin")

    async def check_response(response: httpx2.Response) -> None:
        if response.status_code >= 400:
            # Modern SDK transports may translate HTTP failures into RPC
            # errors; retain the status without URL/header/body diagnostics.
            logging.getLogger(__name__).warning("MCP HTTP response status %d", response.status_code)
        # Decompression would bypass the raw-byte bound. We request identity,
        # and refuse a server which nevertheless sends a compressed body.
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            raise MCPHTTPPolicyError("Compressed MCP response is not supported")
        media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        # Legacy SSE parses only GET. Streamable HTTP additionally parses
        # successful POST SSE using the same bounded SDK EventSource. No
        # error/redirect/DELETE body is exempt, regardless of MIME claims.
        sse_stream = (
            media == "text/event-stream"
            and response.status_code == 200
            and (
                response.request.method == "GET"
                or streamable_http
                and response.request.method == "POST"
            )
        )
        if not sse_stream:
            try:
                length = int(response.headers.get("content-length", "0"))
            except ValueError as exc:
                raise MCPHTTPPolicyError("Invalid MCP response length") from exc
            if length < 0 or length > MAX_HTTP_BODY:
                raise MCPHTTPPolicyError("MCP HTTP body limit exceeded")
            response.stream = _BoundedBody(response.stream)

    def factory(*, headers=None, timeout=None, auth=None) -> httpx2.AsyncClient:
        if auth is not None:
            raise MCPHTTPPolicyError("Interactive MCP authentication is not available")
        return httpx2.AsyncClient(
            headers={**(headers or {}), "Accept-Encoding": "identity"},
            timeout=timeout or httpx2.Timeout(30.0, read=300.0),
            trust_env=False,
            follow_redirects=False,
            max_redirects=5,
            event_hooks={"request": [check_request], "response": [check_response]},
        )

    return factory
