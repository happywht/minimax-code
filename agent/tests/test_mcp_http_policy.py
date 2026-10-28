"""Secret-bearing HTTP uses one explicit origin and no ambient credentials."""

from __future__ import annotations

import asyncio
import logging

import httpx2
import pytest
from mcp_sdk_fixture import loopback, official_server
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, RedirectResponse, StreamingResponse
from starlette.routing import Mount, Route

from minimax_code.mcp.client import MCPClientError
from minimax_code.mcp.http_policy import (
    MAX_HTTP_BODY,
    MCPHTTPPolicyError,
    http_client_factory,
    validate_headers,
    validate_url,
)
from minimax_code.mcp.sdk_client import SDKMCPClient


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/mcp",
        "ftp://localhost/sse",
        "http:///sse",
        "http://user:pass@localhost/sse",
        "http://localhost/sse#fragment",
        "http://localhost:99999/sse",
        "http://localhost/\r\nsecret",
        None,
    ],
)
def test_invalid_urls_rejected(url):
    with pytest.raises(MCPHTTPPolicyError):
        validate_url(url)


@pytest.mark.parametrize(
    "url", ["http://localhost/sse", "http://127.0.0.1/sse", "https://10.0.0.1/mcp?state=kept"]
)
def test_explicit_private_and_loopback_configuration_allowed(url):
    assert validate_url(url) == url


@pytest.mark.parametrize(
    "headers,token",
    [
        ({"Authorization": "x", "authorization": "y"}, None),
        ({"Authorization": "x"}, "token"),
        ({"X-Test": "x\r\ny"}, None),
        ({"Host": "other"}, None),
        ({"Mcp-Param-Region": "x"}, None),
        ({"Content-Type": "x"}, None),
        ({"X Bad": "x"}, None),
        ({"X": 1}, None),
        ({}, "a\nb"),
    ],
)
def test_unsafe_headers_rejected(headers, token):
    with pytest.raises(MCPHTTPPolicyError):
        validate_headers(headers, token)


async def test_factory_ignores_proxy_and_rejects_cross_origin_before_send(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    async with http_client_factory("https://example.test/sse")() as client:
        assert not client._trust_env
        for target in (
            "http://example.test/sse",
            "https://other.test/sse",
            "https://example.test:444/sse",
        ):
            with pytest.raises(MCPHTTPPolicyError, match="origin"):
                await client.get(target)


async def test_sdk_cross_origin_redirect_and_endpoint_never_send_secrets(caplog):
    observed = []
    release = asyncio.Event()

    async def receiver(request):
        observed.append(request)
        raise AssertionError("foreign origin must receive no request")

    target = Starlette(routes=[Route("/foreign", receiver, methods=["GET", "POST"])])
    async with loopback(target) as foreign:

        async def redirect(_request):
            return RedirectResponse(foreign + "/foreign?query-secret", status_code=307)

        async def bad_endpoint(_request):
            async def events():
                yield f"event: endpoint\ndata: {foreign}/foreign?query-secret\n\n"
                await release.wait()

            return StreamingResponse(events(), media_type="text/event-stream")

        source = Starlette(routes=[Route("/redirect", redirect), Route("/sse", bad_endpoint)])
        async with loopback(source) as base:
            caplog.set_level(logging.DEBUG)
            for path in ("/redirect", "/sse?configured-query-secret"):
                client = SDKMCPClient(
                    url=base + path,
                    bearer_token="bearer-secret-sentinel",
                    headers={"X-Private": "header-secret-sentinel"},
                    timeout=0.5,
                )
                try:
                    with pytest.raises(MCPClientError) as failure:
                        await client.initialize()
                    for secret in (
                        "query-secret",
                        "bearer-secret-sentinel",
                        "header-secret-sentinel",
                    ):
                        assert secret not in str(failure.value)
                finally:
                    release.set()
                    await client.close()
            assert observed == []
            assert all(
                secret not in caplog.text
                for secret in (
                    "query-secret",
                    "configured-query-secret",
                    "bearer-secret-sentinel",
                    "header-secret-sentinel",
                )
            )


async def test_real_sse_same_origin_redirect_keeps_explicit_headers(monkeypatch):
    server, _, _ = official_server()
    recorded = []
    original = server.sse_app()

    async def record(scope, receive, send):
        if scope["type"] == "http":
            recorded.append((scope["method"], dict(scope["headers"])))
        await original(scope, receive, send)

    async def redirect(_request):
        return RedirectResponse("/sse", status_code=307)

    outer = Starlette(routes=[Route("/redirect", redirect), Mount("/", app=record)])
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    async with loopback(outer) as base:
        client = SDKMCPClient(
            url=base + "/redirect", bearer_token="explicit", headers={"X-Private": "private"}
        )
        try:
            await client.initialize()
            await client.list_tools()
            assert (await client.call_tool("add", {"a": 1, "b": 2})).structuredContent == {
                "result": 3
            }
        finally:
            await client.close()
    assert {method for method, _ in recorded} == {"GET", "POST"}
    assert all(headers.get(b"authorization") == b"Bearer explicit" for _, headers in recorded)
    assert all(headers.get(b"x-private") == b"private" for _, headers in recorded)


async def test_json_and_error_body_limits_include_chunked_responses():
    async def stream(_request):
        async def chunks():
            yield b"x" * MAX_HTTP_BODY
            yield b"overflow"

        return StreamingResponse(chunks(), media_type="application/json")

    source = Starlette(routes=[Route("/json", stream)])
    async with loopback(source) as base:
        async with http_client_factory(base + "/json")() as client:
            with pytest.raises(MCPHTTPPolicyError, match="body limit"):
                await client.get(base + "/json")


async def test_http_factory_response_closes_on_rejected_body():
    # Hooks are the real installed httpx2 API, not the old project's httpx.
    factory = http_client_factory("http://127.0.0.1/sse")
    async with factory() as client:
        response = httpx2.Response(200, headers={"Content-Length": str(MAX_HTTP_BODY + 1)})
        with pytest.raises(MCPHTTPPolicyError, match="body limit"):
            await client.event_hooks["response"][0](response)


async def test_401_attach_diagnostic_keeps_status_without_url_header_body_secrets(
    caplog, monkeypatch
):
    from unittest.mock import AsyncMock

    from minimax_code import app
    from minimax_code.agent.tools.base import ToolRegistry
    from minimax_code.mcp.registry import MCPRegistry, MCPServerConfig

    async def unauthorized(_request):
        return PlainTextResponse("response-body-secret", status_code=401)

    monkeypatch.setattr(app, "before_executable_extension", AsyncMock())
    caplog.set_level(logging.DEBUG)
    async with loopback(Starlette(routes=[Route("/sse", unauthorized)])) as base:
        registry = MCPRegistry(ToolRegistry())
        assert not await registry.add_server(
            MCPServerConfig(
                name="offline-401",
                transport="sse",
                url=base + "/sse?query-secret",
                bearer_token="bearer-secret",
                headers={"X-Fixture": "header-secret"},
            )
        )
        assert registry.list_servers() == []
    assert "MCP connect failed (HTTPStatusError, HTTP 401)" in caplog.text
    for secret in ("query-secret", "bearer-secret", "header-secret", "response-body-secret"):
        assert secret not in caplog.text


@pytest.mark.parametrize(
    "method,status", [("POST", 200), ("POST", 202), ("POST", 400), ("GET", 500), ("GET", 307)]
)
async def test_sdk_buffered_responses_cannot_evade_cap_with_sse_content_type(method, status):
    from mcp.shared._httpx_utils import request_within_origin

    followed = []

    async def disguised(_request):
        async def chunks():
            yield b"x" * MAX_HTTP_BODY
            yield b"overflow"

        return StreamingResponse(
            chunks(),
            status_code=status,
            media_type="text/event-stream",
            headers={"Location": "/after"} if status == 307 else None,
        )

    async def after(_request):
        followed.append(1)
        return PlainTextResponse("redirect body must be bounded first")

    asgi = Starlette(
        routes=[Route("/disguised", disguised, methods=["GET", "POST"]), Route("/after", after)]
    )
    async with loopback(asgi) as base:
        async with http_client_factory(base + "/disguised")() as client:
            # This is the exact official helper used by SSE POST writes and
            # redirect draining; a MIME label must not bypass its aread cap.
            with pytest.raises(MCPHTTPPolicyError, match="body limit"):
                await request_within_origin(client, method, base + "/disguised")
    assert followed == []
