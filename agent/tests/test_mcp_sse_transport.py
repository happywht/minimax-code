"""Tests for :class:`SSETransport`.

These tests use hand-rolled fakes instead of ``httpx.MockTransport`` so we can
drive the SSE byte stream with ``asyncio.Event`` s without relying on the
mock transport's streaming behaviour.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from minimax_code.mcp.transport import MCPTransportError, SSETransport


class _FakeResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def raise_for_status(self) -> None:
        return

    async def aiter_lines(self) -> Any:
        for line in self._lines:
            if asyncio.iscoroutine(line):
                line = await line
            yield line

    async def aclose(self) -> None:
        return


class _FakeClient:
    def __init__(
        self,
        *,
        lines: list[str],
        captured: dict[str, Any],
        post_done: asyncio.Event,
    ) -> None:
        self._lines = lines
        self._captured = captured
        self._post_done = post_done

    async def get(self, _url: str, *, headers: dict[str, str] | None = None) -> _FakeResponse:
        return _FakeResponse(self._lines)

    async def post(
        self,
        _url: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _FakeResponse:
        self._captured["body"] = json
        self._captured["headers"] = headers
        self._post_done.set()
        return _FakeResponse([])

    async def aclose(self) -> None:
        return


async def _endpoint_then_result(response_ready: asyncio.Event) -> str:
    await response_ready.wait()
    result = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    return f"data: {json.dumps(result)}\n"


@pytest.mark.asyncio
async def test_sse_transport_happy_path() -> None:
    response_ready = asyncio.Event()
    post_done = asyncio.Event()
    captured: dict[str, Any] = {}
    lines = [
        "event: endpoint\n",
        "data: /messages?session_id=abc\n",
        "\n",
        _endpoint_then_result(response_ready),
        "\n",
    ]
    client = _FakeClient(lines=lines, captured=captured, post_done=post_done)
    sse = SSETransport("http://example.com/sse", client=client, timeout=1.0)  # type: ignore[arg-type]

    await sse.start()
    await sse.send({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}})
    await asyncio.wait_for(post_done.wait(), timeout=1.0)

    response_ready.set()
    messages = []
    async for msg in sse.messages():
        messages.append(msg)
        break

    await sse.close()

    assert captured["body"]["method"] == "ping"
    assert messages[0]["result"]["ok"] is True


@pytest.mark.asyncio
async def test_sse_transport_sends_auth_and_custom_headers() -> None:
    response_ready = asyncio.Event()
    post_done = asyncio.Event()
    captured: dict[str, Any] = {}
    lines = [
        "event: endpoint\n",
        "data: /messages?session_id=abc\n",
        "\n",
        _endpoint_then_result(response_ready),
        "\n",
    ]
    client = _FakeClient(lines=lines, captured=captured, post_done=post_done)
    sse = SSETransport(
        "http://example.com/sse",
        client=client,  # type: ignore[arg-type]
        headers={"X-Custom": "yes"},
        bearer_token="secret",
        timeout=1.0,
    )

    await sse.start()
    await sse.send({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}})
    await asyncio.wait_for(post_done.wait(), timeout=1.0)
    response_ready.set()
    async for _msg in sse.messages():
        break
    await sse.close()

    headers = {k.lower(): v for k, v in captured["headers"].items()}
    assert headers.get("authorization") == "Bearer secret"
    assert headers.get("x-custom") == "yes"


@pytest.mark.asyncio
async def test_sse_transport_close_is_idempotent() -> None:
    sse = SSETransport("http://example.com/sse", client=_FakeClient(lines=[], captured={}, post_done=asyncio.Event()))  # type: ignore[arg-type]
    await sse.close()
    await sse.close()


@pytest.mark.asyncio
async def test_sse_transport_errors_on_missing_endpoint() -> None:
    sse = SSETransport(
        "http://example.com/sse",
        client=_FakeClient(lines=["data: {}\n", "\n"], captured={}, post_done=asyncio.Event()),  # type: ignore[arg-type]
        timeout=0.2,
    )
    with pytest.raises(MCPTransportError, match="endpoint advertisement timed out"):
        await sse.start()
    await sse.close()
