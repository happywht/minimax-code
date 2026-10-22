"""File overlays prevent shell escape, rather than report it afterward."""

from __future__ import annotations

import sys

import pytest

from minimax_code.agent.tools import get_default_registry
from minimax_code.agent.tools.subagents import SANDBOX_PROTOCOL_PROMPT
from minimax_code.execution import ExecutionDeniedError, spawn_exec, spawn_shell
from minimax_code.workspace_ctx import (
    reset_current_root,
    reset_sandbox,
    set_current_root,
    set_sandbox,
)


@pytest.mark.parametrize("body", [
    "open('new.txt', 'w').write('escape')",
    "open('existing.txt', 'w').write('overwrite')",
    "import os; os.remove('existing.txt')",
    "print('apparently read only')",
])
async def test_overlay_cannot_spawn_even_apparently_read_only_commands(tmp_path, body):
    existing = tmp_path / "existing.txt"
    existing.write_text("user data", encoding="utf-8")
    script = tmp_path / "worker.py"
    script.write_text(body, encoding="utf-8")
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    root_token, sandbox_token = set_current_root(tmp_path), set_sandbox(overlay)
    try:
        result = await get_default_registry().dispatch(
            "exec_command", {"cmd": [sys.executable, str(script)]}
        )
        assert not result.success and "file overlay" in result.error
        # Direct verification/hook/MCP callers cannot bypass the tool guard.
        with pytest.raises(ExecutionDeniedError, match="file overlay"):
            await spawn_exec(sys.executable, str(script))
        with pytest.raises(ExecutionDeniedError, match="file overlay"):
            await spawn_shell("echo blocked")
        assert existing.read_text(encoding="utf-8") == "user data"
        assert not (tmp_path / "new.txt").exists()
    finally:
        reset_sandbox(sandbox_token)
        reset_current_root(root_token)


async def test_trusted_main_workspace_command_remains_available(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text("print('main workspace')", encoding="utf-8")
    token = set_current_root(tmp_path)
    try:
        result = await get_default_registry().dispatch(
            "exec_command", {"cmd": [sys.executable, str(script)]}
        )
        assert result.success, result.error
        assert "main workspace" in result.output["stdout"]
    finally:
        reset_current_root(token)


def test_protocol_prompt_explains_process_restriction():
    assert "unavailable" in SANDBOX_PROTOCOL_PROMPT
    assert "collect_subagent" in SANDBOX_PROTOCOL_PROMPT
    assert "write_file" in SANDBOX_PROTOCOL_PROMPT
