"""Secret-redaction audit (roadmap R17, v0.14.0).

The audit walked every path an API key can take through the agent:

1. **Log sink** — ``SanitizerFilter`` scrubs credential shapes from
   every record. The R15 wiring attached it to the *root logger*, but
   a logger-level filter only runs for records that logger itself
   emits; records propagated from child loggers (``minimax_code.*`` —
   i.e. every business log line) skipped it and leaked verbatim. R17
   re-attaches the filter at the *handler* level where propagation
   still passes through. The regression test below pins that hole.
2. **RPC envelope** — error responses bypass the logging pipeline
   entirely, so they are audited separately: the ``secrets.*``
   round-trip must never echo the key value, and defensive
   ``str(exc)`` fields are now redacted at the source
   (``handlers_secrets.py``).
3. **LLM errors** — transport ``LLMError`` messages wrap SDK
   exceptions which do not print request headers; the shape test
   below proves the redactor still catches one if a backend ever
   starts including it.

No test here touches the real OS keyring — the secrets module is
monkeypatched with an in-memory fake.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from minimax_code.ipc.protocol import INVALID_PARAMS
from minimax_code.ipc.server import IPCServer
from minimax_code.telemetry.redact import SanitizerFilter

# File-level marker: part of the security-regression suite (R19).
pytestmark = pytest.mark.security

# A realistic credential *shape* — not a real credential.
FAKE_KEY = "sk-ant-AUDIT1234567890abcdefghij"


# ---------------------------------------------------------------------------
# 1. Log sink — propagation must not bypass redaction
# ---------------------------------------------------------------------------


def test_sanitizer_on_handler_scrubs_child_logger_records() -> None:
    """The R17 regression: a record emitted by a child logger and
    propagated to a root handler must be scrubbed. Logger-level filters
    never see propagated records — only handler-level filters do."""
    import io

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    # The exact wiring configure_logging installs (R17): filter on the
    # handler, not (only) on the logger.
    handler.addFilter(SanitizerFilter())

    root = logging.getLogger()
    saved_handlers, saved_level, saved_filters = (
        list(root.handlers),
        root.level,
        list(root.filters),
    )
    try:
        for h in list(root.handlers):
            root.removeHandler(h)
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        logging.getLogger("minimax_code.some.deep.child").error(
            "LLM call failed with api_key=%s", FAKE_KEY
        )
    finally:
        root.removeHandler(handler)
        for h in saved_handlers:
            root.addHandler(h)
        root.setLevel(saved_level)
        for f in saved_filters:
            root.addFilter(f)

    out = stream.getvalue()
    assert FAKE_KEY not in out
    assert "[REDACTED]" in out


def test_configure_logging_attaches_sanitizer_to_every_handler() -> None:
    """After configure_logging(), every installed handler carries the
    SanitizerFilter — the wiring contract that makes the test above
    hold in production."""
    from minimax_code.logging_setup import configure_logging

    root = logging.getLogger()
    saved = {
        "handlers": list(root.handlers),
        "level": root.level,
        "filters": list(root.filters),
    }
    try:
        configure_logging("INFO")
        assert root.handlers, "configure_logging must install handlers"
        for h in root.handlers:
            assert any(isinstance(f, SanitizerFilter) for f in h.filters), (
                f"handler {h!r} has no SanitizerFilter"
            )
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
            h.close()
        for h in saved["handlers"]:
            root.addHandler(h)
        root.setLevel(saved["level"])


# ---------------------------------------------------------------------------
# 2. RPC envelope — secrets.* round-trip never echoes the key
# ---------------------------------------------------------------------------


class _MemSecrets:
    """In-memory stand-in for minimax_code.secrets — no keyring access."""

    def __init__(self) -> None:
        self.value: str | None = None

    def set_api_key(self, value: str) -> None:
        self.value = value

    def has_api_key(self) -> bool:
        return self.value is not None

    def key_source(self) -> str:
        return "keyring" if self.value is not None else "none"

    def clear_api_key(self) -> None:
        self.value = None


@pytest.fixture
def mem_secrets(monkeypatch: pytest.MonkeyPatch) -> _MemSecrets:
    fake = _MemSecrets()
    import minimax_code.secrets as secrets_mod

    monkeypatch.setattr(secrets_mod, "set_api_key", fake.set_api_key)
    monkeypatch.setattr(secrets_mod, "has_api_key", fake.has_api_key)
    monkeypatch.setattr(secrets_mod, "key_source", fake.key_source)
    monkeypatch.setattr(secrets_mod, "clear_api_key", fake.clear_api_key)
    return fake


@pytest.fixture
async def secrets_server() -> IPCServer:
    from minimax_code.config import Config
    from minimax_code.ipc.handlers_secrets import register_secret_handlers

    server = IPCServer(config=Config())
    register_secret_handlers(server)
    return server


async def _rpc(server: IPCServer, method: str, params: Any) -> dict[str, Any]:
    resp = await server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    )
    assert resp is not None
    return resp


@pytest.mark.asyncio
async def test_secrets_set_never_echoes_key(
    mem_secrets: _MemSecrets, secrets_server: IPCServer
) -> None:
    resp = await _rpc(secrets_server, "secrets.set", {"value": FAKE_KEY})
    assert mem_secrets.value == FAKE_KEY  # the write itself happened
    wire = json.dumps(resp)
    assert FAKE_KEY not in wire
    assert "AUDIT1234567890" not in wire  # key body without the prefix too
    assert resp["result"]["configured"] is True


@pytest.mark.asyncio
async def test_secrets_status_and_clear_never_echo_key(
    mem_secrets: _MemSecrets, secrets_server: IPCServer
) -> None:
    await _rpc(secrets_server, "secrets.set", {"value": FAKE_KEY})

    status = await _rpc(secrets_server, "secrets.status", {})
    assert FAKE_KEY not in json.dumps(status)
    assert status["result"]["configured"] is True

    cleared = await _rpc(secrets_server, "secrets.clear", {})
    assert FAKE_KEY not in json.dumps(cleared)
    assert cleared["result"]["configured"] is False
    assert mem_secrets.value is None


@pytest.mark.asyncio
async def test_secrets_set_rejects_bad_params_without_leaking(
    mem_secrets: _MemSecrets, secrets_server: IPCServer
) -> None:
    """Even the INVALID_PARAMS branch must not reflect the payload."""
    resp = await _rpc(
        secrets_server, "secrets.set", {"value": {"nested": FAKE_KEY}}
    )
    assert resp["error"]["code"] == INVALID_PARAMS
    assert FAKE_KEY not in json.dumps(resp)


# ---------------------------------------------------------------------------
# 3. LLM error shapes — the redactor catches key-bearing messages
# ---------------------------------------------------------------------------


def test_transport_error_message_shape_is_redactable() -> None:
    """If a transport ever includes the key in an error message (none do
    today), the log redactor recognizes the shape."""
    from minimax_code.telemetry.redact import redact_value

    msg = f"OpenAI API error: 401 invalid api_key={FAKE_KEY} for request"
    scrubbed = redact_value(msg)
    assert FAKE_KEY not in scrubbed
    assert "[REDACTED]" in scrubbed
