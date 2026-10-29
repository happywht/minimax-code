"""Shared fixtures: offline by default; live tests require an explicit opt-in.

Do not force MockTransport: protocol replay tests must exercise the real SDK
parser against recorded/fake HTTP responses. Socket and credential guards keep
that distinct from spending a developer's API credits.
"""

import ipaddress
import os
import socket
import sys
from pathlib import Path

import pytest

# Make the package importable without an editable install.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def pytest_addoption(parser):
    parser.addoption("--run-live", action="store_true", help="Enable explicitly marked live tests")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-live"):
        for item in items:
            if item.get_closest_marker("live"):
                item.add_marker(pytest.mark.skip(reason="live test requires --run-live"))


def _is_loopback(host):
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="replace")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture
def isolated_http_runtime(monkeypatch):
    """Give an in-process HTTP test its own service lifetime and fences."""
    import asyncio

    from minimax_code import app, scheduler

    monkeypatch.setattr(scheduler, "_SCHEDULER", None)
    monkeypatch.setattr(scheduler, "_SCHEDULER_LOCK", asyncio.Lock())
    monkeypatch.setattr(scheduler, "_SCHEDULER_QUIESCING", False)
    monkeypatch.setattr(app, "_CODEBASE_INDEX_CLOSING", False)
    monkeypatch.setattr(app, "_CODEBASE_INDEXERS", {})
    monkeypatch.setattr(app, "_CODEBASE_INDEXER", None)
    monkeypatch.setattr(app, "_CODEBASE_AUTO_TASKS", set())
    for name in ("_DB_SINGLETON", "_EXECUTION_PROFILES", "_MCP_REGISTRY", "_SUBAGENT_LLM"):
        monkeypatch.setattr(app, name, None)


@pytest.fixture
def bind_test_sessions(monkeypatch, tmp_path):
    """Explicit session rows for handler tests with a mocked conversation DB."""
    from types import SimpleNamespace

    from minimax_code import app

    rows = {}

    async def get(session_id):
        return rows.get(session_id)

    monkeypatch.setattr(app, "get_sessions_dao", lambda: SimpleNamespace(get=get))

    def bind(*ids):
        for session_id in ids:
            rows[session_id] = {"id": session_id, "workspace_path": str(tmp_path)}

    return bind


@pytest.fixture(autouse=True)
def isolated_test_environment(request, monkeypatch, tmp_path):
    if request.node.get_closest_marker("live") and request.config.getoption("--run-live"):
        yield
        return

    import keyring
    from keyring.backends.null import Keyring

    # Individual tests may override these with explicit fake credentials or
    # FakeKeyring. Real OS credential storage is never the default.
    monkeypatch.setattr(keyring, "get_keyring", lambda: Keyring())
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    for name in list(os.environ):
        upper = name.upper()
        if upper.endswith(("_API_KEY", "_APIKEY", "_ACCESS_TOKEN", "_AUTH_TOKEN")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("MINIMAX_CODE_DATA_DIR", str(tmp_path / "agent-data"))
    # In-process ASGI tests use virtual hosts. Production defaults remain
    # loopback-only; test clients still need an explicit bearer credential.
    monkeypatch.setenv("MINIMAX_CODE_ALLOWED_HOSTS", "testserver,test")
    # Ignore a developer's forced mock setting; tests choose their transport.
    monkeypatch.delenv("MINIMAX_CODE_FORCE_MOCK", raising=False)

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo

    def check_address(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback(address[0]):
            raise RuntimeError("External network disabled in offline tests; use a transport fixture")

    def connect(sock, address):
        check_address(sock, address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        check_address(sock, address)
        return original_connect_ex(sock, address)

    def getaddrinfo(host, *args, **kwargs):
        # None and wildcard addresses are used to bind local test servers.
        if host not in (None, "", "0.0.0.0", "::") and not _is_loopback(host):
            raise RuntimeError("External DNS disabled in offline tests; use a transport fixture")
        return original_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    yield
