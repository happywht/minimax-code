"""Cumulative Anthropic usage replay, including late input/cache snapshots."""
from __future__ import annotations

import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from minimax_code.agent.llm import MiniMaxClient
from minimax_code.agent.transports.anthropic_transport import _anthropic_stream_to_chunks
from minimax_code.budget import BudgetExhausted
from minimax_code.budget_ledger import BudgetHandle, budget_scope, estimate_request, normalize_usage
from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase

# Sanitized numeric shape only: (uncached input, cache read, output).
LATE_USAGE = (
    (6302, 161, 61), (986, 6463, 117), (673, 7449, 62),
    (705, 8122, 87), (883, 8827, 87), (607, 9710, 62),
    (386, 10317, 137), (681, 10703, 62), (190, 11384, 356),
)


def reply(start, deltas):
    events = [{"type": "message_start", "message": {
        "id": "offline", "type": "message", "role": "assistant", "content": [],
        "model": "MiniMax-M3", "stop_reason": None, "stop_sequence": None, "usage": start,
    }}]
    events.extend({"type": "message_delta", "usage": usage, "delta": {
        "stop_reason": "end_turn" if index == len(deltas) - 1 else None, "stop_sequence": None,
    }} for index, usage in enumerate(deltas))
    events.append({"type": "message_stop"})
    body = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def client(handler):
    sdk = anthropic.AsyncAnthropic(
        api_key="offline", base_url="https://offline.invalid", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return MiniMaxClient(protocol="anthropic", api_key="offline", mock=False,
                         model="MiniMax-M3", client=sdk)


async def chunks(start, *deltas):
    async def events():
        yield SimpleNamespace(type="message_start", message=SimpleNamespace(usage=start))
        for delta in deltas:
            yield SimpleNamespace(type="message_delta", usage=delta,
                                  delta=SimpleNamespace(stop_reason="end_turn"))
    return [chunk async for chunk in _anthropic_stream_to_chunks(events())]


@pytest.mark.replay
async def test_real_sdk_late_usage_accounts_for_all_sanitized_request_snapshots():
    calls = []

    def handler(request):
        raw_input, cache_read, output = LATE_USAGE[len(calls)]
        calls.append(request)
        return reply({"input_tokens": 0, "output_tokens": 0}, [{
            "input_tokens": raw_input, "cache_read_input_tokens": cache_read,
            "output_tokens": output,
        }])

    llm = client(handler)
    try:
        results = [await llm.chat([{"role": "user", "content": "offline fixture"}], max_tokens=4096)
                   for _ in LATE_USAGE]
        assert sum(result.usage["prompt_tokens"] for result in results) == 84549
        assert sum(result.usage["cached_tokens"] for result in results) == 73136
        assert sum(result.usage["completion_tokens"] for result in results) == 1031
        assert sum(result.usage["total_tokens"] for result in results) == 85580
        assert all(normalize_usage(result.usage)["complete"] for result in results)
        assert len(calls) == 9
    finally:
        await llm.close()


@pytest.mark.replay
async def test_real_sdk_standard_and_partial_cumulative_snapshots_are_not_added():
    snapshots = [
        {"cache_read_input_tokens": 8, "output_tokens": 2},
        {"cache_creation_input_tokens": 3, "output_tokens": 4},
        {"input_tokens": 5, "cache_read_input_tokens": 0, "output_tokens": 4},
        {"input_tokens": None, "cache_creation_input_tokens": None, "output_tokens": 4},
    ]
    llm = client(lambda request: reply({"input_tokens": 10, "output_tokens": 0,
                                       "cache_read_input_tokens": 4, "cache_creation_input_tokens": 6}, snapshots))
    try:
        result = await llm.chat([{"role": "user", "content": "offline"}], max_tokens=32)
        assert result.usage["prompt_tokens"] == 8  # raw5 + read0 + creation3
        assert result.usage["cached_tokens"] == 0
        assert result.usage["cache_creation_tokens"] == 3
        assert result.usage["completion_tokens"] == 4
        assert result.usage["total_tokens"] == 12
    finally:
        await llm.close()


async def test_absent_sdk_defaults_and_null_do_not_replace_prior_snapshots():
    start = anthropic.types.Usage.model_construct(input_tokens=10, output_tokens=0,
                                                 cache_read_input_tokens=4)
    delta = SimpleNamespace(output_tokens=7, input_tokens=999, model_fields_set={"output_tokens"})
    result = await chunks(start, delta, SimpleNamespace(input_tokens=None, output_tokens=None))
    assert result[-1].usage["prompt_tokens"] == 14
    assert result[-1].usage["completion_tokens"] == 7
    assert result[-1].usage["total_tokens"] == 21


async def test_explicit_zero_replaces_prior_counts_and_cache_is_not_double_counted():
    result = await chunks(SimpleNamespace(input_tokens=10, cache_read_input_tokens=4),
                          SimpleNamespace(input_tokens=0, cache_read_input_tokens=0, output_tokens=0))
    assert result[-1].usage["prompt_tokens"] == 0
    assert result[-1].usage["completion_tokens"] == 0
    assert result[-1].usage["total_tokens"] == 0
    assert normalize_usage(result[-1].usage)["complete"]


@pytest.mark.parametrize("missing", ["input", "output", "both"])
async def test_missing_required_final_usage_remains_unknown(missing):
    start = SimpleNamespace(output_tokens=0, **({"input_tokens": 10} if missing == "output" else {}))
    delta = SimpleNamespace(**({"output_tokens": 5} if missing == "input" else {}))
    result = await chunks(start, delta)
    assert not normalize_usage(result[-1].usage)["complete"]
    assert result[-1].usage["total_tokens"] is None
    if missing in {"output", "both"}:
        assert result[-1].usage["completion_tokens"] is None  # start0 is not final output


@pytest.mark.parametrize("field", ["input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"])
@pytest.mark.parametrize("invalid", [True, -1, "3", 1.5])
async def test_invalid_present_counts_are_unknown_and_late_valid_counts_recover(field, invalid):
    start = SimpleNamespace(input_tokens=10, cache_read_input_tokens=4, cache_creation_input_tokens=2)
    result = await chunks(start, SimpleNamespace(**{field: invalid, **({} if field == "output_tokens" else {"output_tokens": 5})}),
                          SimpleNamespace(**{field: 0}))
    assert not normalize_usage(result[-2].usage)["complete"]
    assert result[-2].usage["total_tokens"] is None
    assert normalize_usage(result[-1].usage)["complete"]


@pytest.mark.replay
async def test_late_usage_settlement_blocks_sixth_estimated_request_before_dispatch(tmp_path):
    db = AsyncDatabase(tmp_path / "late-usage.db")
    await db.connect()
    await db.migrate()
    await SessionsDAO(db).create(id="offline")
    run, _ = await AgentRunsDAO(db).admit_run(
        session_id="offline", request={"mode": "chat", "content": "fixture"}, title="fixture",
        metadata={"budget": {"limit_tokens": 60000},
                  "model_snapshot": {"provider_id": "builtin-minimax", "model": "MiniMax-M3"}},
    )
    handle = await BudgetHandle.for_run(db, run["id"])
    calls = []

    def handler(request):
        raw_input, cache_read, output = LATE_USAGE[len(calls)]
        calls.append(json.loads(request.content))
        return reply({"input_tokens": 0, "output_tokens": 0}, [{
            "input_tokens": raw_input, "cache_read_input_tokens": cache_read, "output_tokens": output,
        }])

    llm = client(handler)
    try:
        with budget_scope(handle):
            for _ in range(5):
                await llm.chat([{"role": "user", "content": "fixture"}], max_tokens=4096)
            before = await handle.dao.attempts(handle.family_id)
            assert len(before) == 5 and all(row["state"] == "settled" for row in before)
            assert [row["known_tokens"] for row in before] == [6524, 7566, 8184, 8914, 9797]
            snapshot = await handle.snapshot()
            assert snapshot["known_tokens"] == 40985 and snapshot["reserved_tokens"] == 0
            body = {key: value for key, value in calls[-1].items() if key != "stream"}
            assert before[-1]["estimated_tokens"] == estimate_request(body)
            body["messages"][0]["content"][0]["text"] = ""
            empty_estimate = estimate_request(body)
            padding = "x" * (42207 - empty_estimate)
            body["messages"][0]["content"][0]["text"] = padding
            assert estimate_request(body) == 42207
            with pytest.raises(BudgetExhausted, match="family_ceiling"):
                await llm.chat([{"role": "user", "content": padding}], max_tokens=4096)
            assert len(calls) == 5  # admission rejected before actual SDK/HTTP dispatch
            assert await handle.dao.attempts(handle.family_id) == before
            snapshot = await handle.snapshot()
            assert snapshot["known_tokens"] == 40985 and snapshot["reserved_tokens"] == 0
    finally:
        await llm.close()
        await db.close()
