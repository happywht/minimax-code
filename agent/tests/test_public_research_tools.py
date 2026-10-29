"""Offline tests for public research permissions and pinned address boundary."""
from __future__ import annotations

import json
import socket
from unittest.mock import AsyncMock

import pytest

from minimax_code.agent.core import AgentCore
from minimax_code.agent.tools import ToolRegistry
from minimax_code.agent.tools import public_research as research
from minimax_code.agent.tools.base import Tool, ToolResult
from minimax_code.execution import ExecutionPolicy, execution_scope


def test_dns_rejects_any_private_address_and_url_credentials(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 443)),
    ])
    with pytest.raises(ValueError, match="non-public"):
        research._addresses("public.example")
    for url in ("http://example.com", "https://u:p@example.com", "https://localhost", "https://example.com:444"):
        with pytest.raises(ValueError):
            research._url(url)


def test_pinned_socket_uses_verified_address_and_tls_hostname(monkeypatch):
    seen = {}

    class Raw:
        def settimeout(self, timeout): pass
        def connect(self, target): seen["target"] = target
        def close(self): pass

    class Context:
        def wrap_socket(self, raw, server_hostname):
            seen["server_hostname"] = server_hostname
            return raw

    monkeypatch.setattr(socket, "socket", lambda *a: Raw())
    monkeypatch.setattr(research.ssl, "create_default_context", lambda: Context())
    conn = research._PinnedHTTPS("public.example", socket.AF_INET, "93.184.216.34", 3)
    conn.connect()
    assert seen == {"target": ("93.184.216.34", 443), "server_hostname": "public.example"}


def test_rechecks_redirect_and_tries_next_public_address(monkeypatch):
    def resolve(host, *args, **kwargs):
        if host == "127.0.0.1":
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 443))]
        return [(socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("2606:4700:4700::1111", 443, 0, 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    calls = []

    class Response:
        status = 302
        def getheaders(self): return [("Location", "https://127.0.0.1/secret")]
        def read1(self, n): return b""

    class Connection:
        sock = None
        def __init__(self, host, family, address, timeout): self.address = address
        def request(self, *a, **kw):
            calls.append(self.address)
            if ":" in self.address:
                raise OSError("IPv6 unavailable")
        def getresponse(self): return Response()
        def close(self): pass

    monkeypatch.setattr(research, "_PinnedHTTPS", Connection)
    with pytest.raises(ValueError, match="non-public"):
        research._fetch("https://example.com/")
    assert calls == ["2606:4700:4700::1111", "93.184.216.34"]


async def test_page_search_bounds_and_missing_configuration(monkeypatch):
    monkeypatch.delenv("MINIMAX_CODE_SEARCH_URL", raising=False)
    missing = await research.SearchPublicWebTool().run("topic")
    assert not missing.success and "MINIMAX_CODE_SEARCH_URL" in missing.error

    def fetch(url):
        if "search" in url:
            body = json.dumps({"results": [{"title": "Result", "url": "https://example.com/a", "content": "s" * 900}]}).encode()
            return url, {"content-type": "application/json"}, body, False
        return "https://example.com/final", {"content-type": "text/html"}, b"<title>Title</title><script>hidden</script><p>evidence</p>", False

    monkeypatch.setattr(research, "_fetch", fetch)
    monkeypatch.setenv("MINIMAX_CODE_SEARCH_URL", "https://search.example/search")
    with execution_scope(ExecutionPolicy(read_only=True)):
        search = await research.SearchPublicWebTool().run("topic")
        page = await research.ReadPublicPageTool().run("https://example.com")
    assert search.success and search.output["results"][0]["snippet_truncated"]
    assert page.success and page.output["title"] == "Title" and page.output["excerpt"] == "evidence"
    assert page.output["url"] == "https://example.com/final" and "untrusted" in page.output["notice"].lower()
    assert page.output["accessed_at"] and page.output["truncated"] is False


@pytest.mark.parametrize(("rules", "gater", "consent", "allowed"), [
    ({}, False, False, False), ({}, True, True, True), ({}, True, False, False),
    ({"network": "deny", "read_public_page": "allow"}, True, True, False),
    ({"network": "allow"}, False, False, True),
])
async def test_network_permission_gate(rules, gater, consent, allowed):
    class Page(Tool):
        name = "read_public_page"
        parameters = {"type": "object", "properties": {}}
        async def run(self): return ToolResult.ok("read")

    class Store:
        def lookup(self, name): return {"action": rules[name]} if name in rules else None

    registry = ToolRegistry()
    registry.register(Page())
    gate = type("Gate", (), {"request_consent": AsyncMock(return_value=consent)})() if gater else None
    core = AgentCore(llm=object(), registry=registry, permission_store=Store(), permission_gater=gate)
    result = await core._dispatch_tool({"name": "read_public_page", "arguments": {}})
    assert result.success is allowed


async def test_restricted_execution_never_registers_or_dispatches_host_network(monkeypatch):
    from minimax_code import process_context

    monkeypatch.setattr(process_context, "current_process_target", lambda: object())
    registry = ToolRegistry()
    research.register_public_research_tools(registry)
    assert not registry.has("search_public_web") and not registry.has("read_public_page")
    result = await research.ReadPublicPageTool().run("https://example.com")
    assert not result.success and "restricted execution" in result.error
