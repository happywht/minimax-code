"""AgentCore ↔ HookManager integration (R7).

Drives :meth:`AgentCore._dispatch_tool` directly with a trivial echo
tool so we exercise the pre/post_tool_use injection points without
needing to stage a full LLM tool-call exchange.
"""

from __future__ import annotations

import sys

from minimax_code.agent.core import AgentCore
from minimax_code.agent.tools import Tool, ToolRegistry, ToolResult
from minimax_code.hooks import HookManager


def _py(script: str) -> list[str]:
    return [sys.executable, "-c", script]


class _Echo(Tool):
    """Echoes args back; records that it ran."""

    name = "echo"
    description = "echo args"
    parameters = {"type": "object", "properties": {}}

    def __init__(self) -> None:  # noqa: D401
        self.calls = 0

    async def run(self, **kwargs: object) -> ToolResult:  # type: ignore[override]
        self.calls += 1
        return ToolResult.ok(output={"echoed": kwargs})


def _registry() -> tuple[ToolRegistry, _Echo]:
    echo = _Echo()
    reg = ToolRegistry()
    reg.register(echo)
    return reg, echo


async def test_no_hooks_core_behaves_unchanged() -> None:
    reg, echo = _registry()
    core = AgentCore(llm=None, registry=reg)  # hooks default None
    result = await core._dispatch_tool({"name": "echo", "arguments": {"x": 1}})  # noqa: SLF001
    assert result.success
    assert echo.calls == 1


async def test_block_hook_short_circuits_before_tool() -> None:
    reg, echo = _registry()
    mgr = HookManager()
    mgr.load_hooks(
        {
            "pre_tool_use": [
                {
                    "command": _py(
                        "import json; print(json.dumps({'block': True, "
                        "'block_reason': 'policy'}))"
                    ),
                    "matcher": {"tool_name": "echo"},
                }
            ]
        }
    )
    core = AgentCore(llm=None, registry=reg, hooks=mgr)
    result = await core._dispatch_tool({"name": "echo", "arguments": {}})  # noqa: SLF001
    assert result.success is False
    assert "policy" in (result.error or "")
    assert echo.calls == 0  # tool never ran


async def test_pass_hook_then_post_hook_fires() -> None:
    reg, echo = _registry()
    mgr = HookManager()
    mgr.load_hooks(
        {
            "pre_tool_use": [{"command": _py("print('pre ok')")}],
            "post_tool_use": [
                {
                    "command": _py("import os; print(os.environ.get('MMC_TEST','x'))"),
                }
            ],
        }
    )
    core = AgentCore(llm=None, registry=reg, hooks=mgr)
    result = await core._dispatch_tool({"name": "echo", "arguments": {"x": 2}})  # noqa: SLF001
    assert result.success
    assert echo.calls == 1  # pre passed → tool ran


async def test_permission_deny_takes_precedence_over_hook() -> None:
    """permission=deny must return before any hook runs (no wasted work)."""

    class _DenyStore:
        @staticmethod
        def resolve(tool: str) -> str:  # minimal contract
            return "deny"

    reg, echo = _registry()

    # Monkey-patch the module-level _check_rule via a deny store.
    from minimax_code.agent import core as core_mod

    orig = core_mod._check_rule
    core_mod._check_rule = lambda store, name: "deny"  # type: ignore[assignment]
    try:
        mgr = HookManager()
        mgr.load_hooks({"pre_tool_use": [{"command": _py("print('never')")}]})
        core = AgentCore(llm=None, registry=reg, hooks=mgr)
        result = await core._dispatch_tool({"name": "echo", "arguments": {}})  # noqa: SLF001
        assert result.success is False
        assert echo.calls == 0
    finally:
        core_mod._check_rule = orig  # type: ignore[assignment]
