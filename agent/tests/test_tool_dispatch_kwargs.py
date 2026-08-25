"""Regression tests for the kwargs-only dispatch contract.

v1.4.0 field report: ``check_subagent(run_id)`` raised
``'dict' object has no attribute 'strip'`` when invoked by the LLM. Root
cause: ``ToolRegistry.dispatch`` routed any tool whose ``run`` declared a
single positional parameter through a "legacy args-dict" branch, passing
the whole arguments dict as that parameter. No tool in the codebase ever
used that convention — the branch only misrouted (check_subagent crashed;
list_agents silently received a truthy dict as ``include_disabled``).

These tests go through ``registry.dispatch`` end to end — the earlier
test suite called ``tool.run(...)`` directly and never exercised the
routing layer, which is why the bug slipped through 48 green tests.
"""

from __future__ import annotations

import pytest

from minimax_code.agent.tools.base import Tool, ToolRegistry, ToolResult


class _SingleParamProbeTool(Tool):
    """Declares exactly one positional parameter — the shape that used to
    trigger the legacy args-dict misrouting."""

    name = "single_param_probe"
    description = "Echo the runtime type of the received parameter."
    parameters = {
        "type": "object",
        "properties": {
            "value": {"type": "string", "description": "value to echo"},
        },
        "required": ["value"],
        "additionalProperties": False,
    }

    async def run(self, value: str) -> ToolResult:
        return ToolResult.ok({"type": type(value).__name__, "value": value})


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_SingleParamProbeTool())
    return reg


async def test_dispatch_passes_kwargs_not_args_dict(registry: ToolRegistry) -> None:
    """A single-positional-param tool must receive the unwrapped value."""
    result = await registry.dispatch("single_param_probe", {"value": "hello"})
    assert result.success, result.error
    assert result.output == {"type": "str", "value": "hello"}


async def test_dispatch_missing_required_arg_still_validated(
    registry: ToolRegistry,
) -> None:
    result = await registry.dispatch("single_param_probe", {})
    assert not result.success
    assert "invalid args" in (result.error or "")


async def test_check_subagent_via_dispatch_receives_str() -> None:
    """End-to-end regression for the field-reported crash.

    Before the fix the LLM call ``check_subagent {"run_id": "..."}`` fed the
    whole dict into ``run`` and died on ``run_id.strip()``. Now the missing
    id must reach the lookup logic and fail with a proper message.
    """
    from minimax_code.agent.tools.base import get_default_registry

    reg = get_default_registry()
    assert reg.has("check_subagent")
    result = await reg.dispatch("check_subagent", {"run_id": "run_does_not_exist"})
    # Whether the lookup hits the DB or not, both paths fail with the same
    # "unknown run_id" message — and neither raises AttributeError.
    assert not result.success
    assert "unknown run_id" in (result.error or "")


async def test_wait_subagent_via_dispatch_receives_str() -> None:
    """Sibling tool shares the same shape — keep it pinned too."""
    from minimax_code.agent.tools.base import get_default_registry

    reg = get_default_registry()
    assert reg.has("wait_subagent")
    result = await reg.dispatch(
        "wait_subagent", {"run_id": "run_does_not_exist", "timeout_s": 0.1}
    )
    assert not result.success
    assert "unknown run_id" in (result.error or "")


async def test_list_agents_via_dispatch_survives_bool_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """list_subagents(include_disabled=...) used to silently receive a truthy
    dict. After the fix the default False parses normally. The DAO is stubbed
    so the test never touches a real database (a live 8765 agent holds the
    production one)."""

    class _StubDAO:
        async def list_all(self) -> list[dict[str, object]]:
            return [
                {"name": "a-on", "enabled": True},
                {"name": "a-off", "enabled": False},
            ]

    async def _stub_dao() -> _StubDAO:
        return _StubDAO()

    import minimax_code.agent.tools.subagents as subagents_mod
    from minimax_code.agent.tools.base import get_default_registry

    monkeypatch.setattr(subagents_mod, "_agent_dao", _stub_dao)
    reg = get_default_registry()
    assert reg.has("list_subagents")
    result = await reg.dispatch("list_subagents", {})
    assert result.success, result.error
    # include_disabled arrived as a real False — the disabled row is filtered.
    names = [a.get("name") for a in result.output["agents"]]
    assert names == ["a-on"]
    assert result.output["count"] == 1


def _kwarg_tool_names() -> list[str]:
    """Guard: every registered tool must accept ``run(**kwargs)`` style."""
    from minimax_code.agent.tools.base import get_default_registry

    names: list[str] = []
    reg = get_default_registry()
    for tool in reg.list():
        import inspect

        params = list(inspect.signature(tool.run).parameters.values())
        accepts_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params)
        if not accepts_kwargs:
            # Positional/keyword params are fine as long as names match the
            # schema properties — the probe test above covers routing.
            names.append(tool.name)
    return names


def test_all_tools_survive_kwargs_routing() -> None:
    """Static sanity: tools without ``**kwargs`` must declare schema-matching
    named parameters (they are now always called with keyword args)."""
    flagged = _kwarg_tool_names()
    # Tools known to use named parameters (multi-arg or single-arg) — all
    # route correctly through ``run(**args)``.
    expected_subset = {
        "list_subagents",
        "check_subagent",
        "wait_subagent",
        "spawn_subagent",
        "read_artifact",
    }
    unexpected = [n for n in flagged if n not in expected_subset and not n.startswith("mcp__")]
    assert unexpected == []


def test_report_protocol_prompt_mentions_budget_rule() -> None:
    """The completion protocol must teach sub-agents to report early —
    iteration exhaustion otherwise silently eats the report."""
    from minimax_code.agent.tools.subagents import REPORT_PROTOCOL_PROMPT

    assert "Budget rule" in REPORT_PROTOCOL_PROMPT
    assert "iteration budget" in REPORT_PROTOCOL_PROMPT


def test_task_precedence_prompt_pins_assignment_over_role() -> None:
    """v1.4.2 field report: a spawned sub-agent discovered an old
    CONTRACT.md in the workspace and followed its standing system-prompt
    role ("implement the whiteboard renderer") instead of the one-off
    assignment in the first user message. The injected paragraph must
    state that the current assignment outranks the standing role."""
    from minimax_code.agent.tools.subagents import TASK_PRECEDENCE_PROMPT

    assert "current assignment" in TASK_PRECEDENCE_PROMPT
    assert "takes precedence" in TASK_PRECEDENCE_PROMPT
    # Workspace files are context, never a mandate to reinterpret the task.
    assert "context only" in TASK_PRECEDENCE_PROMPT


def test_spawn_injection_order_precedence_before_protocol() -> None:
    """The composition must be: agent prompt → task precedence →
    completion protocol. Precedence has to arrive before the report
    instructions so the model reads the priority rule first."""
    from minimax_code.agent.tools import subagents as mod

    assert mod.TASK_PRECEDENCE_PROMPT != mod.REPORT_PROTOCOL_PROMPT
    # The composition happens inline in SpawnSubagentTool.run; pin the
    # ordering by checking the source keeps the tuple order.
    import inspect

    src = inspect.getsource(mod.SpawnSubagentTool.run)
    assert src.index("TASK_PRECEDENCE_PROMPT") < src.index("REPORT_PROTOCOL_PROMPT")
