"""Persisted agent configuration reaches the existing admitted Native core."""
from __future__ import annotations

import pytest
from test_agent_execution import agent_env as agent_env
from test_agent_execution import invoke
from test_skill_execution import skill_env as skill_env

from minimax_code.ipc.handlers_agents import _config_from_row


@pytest.mark.parametrize("method", ["agent.invoke", "agent.spawn_subagent"])
async def test_profile_passes_extended_supported_config_fields(agent_env, method):
    state = agent_env
    await state.agents.upsert(name="worker", system_prompt="Precise agent", max_iterations=200,
                              temperature=0.7, tool_allowlist=["read_file"])
    ctx = await invoke(state, method)
    ctx.reply_error.assert_not_awaited()
    row = await state.dao.get_run(ctx.reply.await_args.args[0]["run_id"])
    profile = row["metadata"]["agent_execution"]
    assert profile["max_iterations"] == 200 and profile["temperature"] == pytest.approx(0.7)
    assert profile["instructions"] == "Precise agent" and profile["allowlist"] == ["read_file"]
    assert state.calls[0]["temperature"] == pytest.approx(0.7)


@pytest.mark.parametrize("method", ["agent.invoke", "agent.spawn_subagent"])
async def test_skill_dependencies_are_explicitly_unsupported_before_admission(agent_env, method):
    state = agent_env
    await state.agents.upsert(name="worker", system_prompt="x", skills=["commit-helper"])
    ctx = await invoke(state, method)
    assert "skill dependencies" in str(ctx.reply_error.await_args)
    assert not state.calls and not await state.dao.list_runs()
    assert not (state.source / ".minimax").exists()


async def test_native_failure_closes_durable_run_not_a_shadow_progress_task(agent_env, monkeypatch):
    from minimax_code.agent.core import AgentCore
    state = agent_env
    async def boom(*args, **kwargs):
        raise RuntimeError("controlled core failure")
    monkeypatch.setattr(AgentCore, "run", boom)
    ctx = await invoke(state)
    ctx.reply.assert_not_awaited()
    assert ctx.reply_error.await_count == 1
    row = (await state.dao.list_runs())[0]
    assert row["status"] == "failed"
    assert (await state.dao.result_payload(row["id"]))["partial"]
    assert state.clients[0]._transport._client is None


def test_config_from_row_keeps_defaults_for_sparse_rows() -> None:
    """Rows without extended columns must keep the dataclass defaults."""
    cfg = _config_from_row(
        {
            "name": "legacy",
            "system_prompt": "old",
            "tool_allowlist": ["read_file"],
            "model": None,
            "id": "agent_abc",
        }
    )
    assert cfg.max_iterations == 100  # dataclass default (P0-3 v1.5.3), not None
    assert cfg.temperature is None
    assert cfg.skills is None
    assert cfg.tags is None
    assert cfg.enabled is True
    assert cfg.name == "legacy"


def test_config_from_row_coerces_types() -> None:
    """SQLite integers/floats come back as int/float — the helper coerces."""
    cfg = _config_from_row(
        {
            "name": "typed",
            "max_iterations": 3,
            "temperature": 1,
            "skills": ["a"],
            "tags": ["b"],
            "enabled": 0,
        }
    )
    assert cfg.max_iterations == 3
    assert cfg.temperature == pytest.approx(1.0)
    assert cfg.skills == ["a"]
    assert cfg.tags == ["b"]
    assert cfg.enabled is False
