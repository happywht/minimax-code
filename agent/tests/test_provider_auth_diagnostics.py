from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import anthropic
import httpx
import openai
import pytest

from minimax_code.agent.types import LLMAuthenticationError
from minimax_code.agent.transports.anthropic_transport import AnthropicTransport
from minimax_code.agent.transports.openai_transport import OpenAITransport
from minimax_code.runs import get_run_service
from test_run_service import native as native_fixture, send  # noqa: F401
from test_budget_ledger import ledger as ledger_fixture  # noqa: F401

native = native_fixture
ledger = ledger_fixture

SECRET = "do-not-log-secret-or-body"


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_sdk_auth_error_is_sanitized_before_agent_events(protocol, monkeypatch, ledger):
    from minimax_code.budget_ledger import BudgetHandle, budget_scope

    _, dao, _ = ledger
    monkeypatch.setattr("minimax_code.budget_ledger.estimate_request", lambda _: 100)
    import minimax_code.app as app

    monkeypatch.setattr(app, "ensure_breaker_registry", lambda: None)
    response = httpx.Response(401, request=httpx.Request("POST", "https://example.invalid"),
                              headers={"X-Secret": SECRET})
    if protocol == "openai":
        error = openai.AuthenticationError(SECRET, response=response, body={"secret": SECRET})
        async def create(**kwargs):
            raise error
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        transport = OpenAITransport(api_key="unused", base_url="https://example.invalid", client=client)
    else:
        error = anthropic.AuthenticationError(SECRET, response=response, body={"secret": SECRET})
        class Context:
            async def __aenter__(self):
                raise error
            async def __aexit__(self, *args):
                return False
        client = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kwargs: Context()))
        transport = AnthropicTransport(api_key="unused", base_url="https://example.invalid", client=client)
    def with_options(**kwargs):
        assert kwargs == {"max_retries": 0}
        return client
    client.with_options = with_options
    with budget_scope(BudgetHandle(dao, "family", "run")):
        with pytest.raises(LLMAuthenticationError) as caught:
            async for _ in transport.stream_chat([], model="m"):
                pass
    attempts = await dao.attempts("family")
    assert len(attempts) == 1 and attempts[0]["state"] == "unknown"
    assert attempts[0]["reason"] == "AuthenticationError"
    assert SECRET not in str(attempts)
    assert SECRET not in str(caught.value)
    assert caught.value.__suppress_context__
    assert caught.value.status_code == 401
    assert "凭据" in str(caught.value)


async def test_native_run_persists_safe_auth_error_and_never_retries(native, caplog, monkeypatch):
    from minimax_code.permissions import PermissionStore

    monkeypatch.setattr("minimax_code.ipc.handlers_permissions._ensure_permission_store",
                        AsyncMock(return_value=PermissionStore(None)))
    calls = 0
    async def fail():
        nonlocal calls
        calls += 1
        raise LLMAuthenticationError()
    native.tool = fail
    ctx = await send(native)
    assert calls == 1, ctx.reply_error.await_args_list
    code, message = ctx.reply_error.await_args.args[:2]
    assert "认证失败" in message and "凭据" in message
    runs = await native.dao.list_runs(session_id="one")
    assert len(runs) == 1 and runs[0]["status"] == "failed"
    assert "认证失败" in runs[0]["error"]
    assert SECRET not in str(runs[0]) + caplog.text + str(ctx.emit.await_args_list)
    assert (await native.dao.result_payload(runs[0]["id"]))["verification"]["verdict"] == "unknown"
    assert not get_run_service(native.server)._jobs.get("one")
