"""Tests for v0.5.0 terminal process hardening.

Coverage:

* :class:`TestBuildSafeEnv` — environment variable whitelist / blacklist
* :class:`TestIsDangerousCmd` — expanded deny list
* :class:`TestArgLimits` — argument count and length enforcement
* :class:`TestExecCommandHardened` — integration via ToolResult
"""

from __future__ import annotations

import os

import pytest

from minimax_code.agent.tools.terminal import (
    _MAX_ARG_LEN,
    _MAX_ARGS,
    ExecCommandTool,
    _build_safe_env,
    _is_dangerous_cmd,
)

# File-level marker: part of the security-regression suite (R19).
pytestmark = pytest.mark.security

# ---------------------------------------------------------------------------
# _build_safe_env
# ---------------------------------------------------------------------------


class TestBuildSafeEnv:
    def test_whitelisted_keys_preserved(self) -> None:
        """Keys in the whitelist that exist in os.environ are kept."""
        env = _build_safe_env()
        # PATH should almost always exist.
        if "PATH" in os.environ:
            assert "PATH" in env

    def test_blocked_prefixes_excluded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Environment variables matching blocked prefixes are dropped."""
        monkeypatch.setenv("MINIMAX_API_KEY", "sk-secret")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
        monkeypatch.setenv("SSH_PRIVATE_KEY", "ssh-key")
        monkeypatch.setenv("MY_PASSWORD", "pw123")
        monkeypatch.setenv("OPENAI_API_KEY", "oai-key")
        env = _build_safe_env()
        assert "MINIMAX_API_KEY" not in env
        assert "AWS_SECRET_ACCESS_KEY" not in env
        assert "SSH_PRIVATE_KEY" not in env
        assert "MY_PASSWORD" not in env
        assert "OPENAI_API_KEY" not in env

    def test_extra_vars_merged(self) -> None:
        """User-provided extra vars are merged (if not blocked)."""
        env = _build_safe_env({"MY_CUSTOM_VAR": "hello"})
        assert env["MY_CUSTOM_VAR"] == "hello"

    def test_extra_blocked_vars_dropped(self) -> None:
        """User-provided extra vars matching blocked prefixes are dropped."""
        env = _build_safe_env({"PASSWORD": "secret", "TOKEN": "abc"})
        assert "PASSWORD" not in env
        assert "TOKEN" not in env

    def test_pythonunbuffered_always_set(self) -> None:
        env = _build_safe_env()
        assert env.get("PYTHONUNBUFFERED") == "1"
        assert "utf-8" in env.get("PYTHONIOENCODING", "")

    def test_does_not_leak_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Even if the real env has API keys, _build_safe_env must not."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
        monkeypatch.setenv("GOOGLE_API_KEY", "google-secret")
        env = _build_safe_env()
        for _key, value in env.items():
            assert "secret" not in value.lower()
            assert "sk-ant" not in value.lower()


# ---------------------------------------------------------------------------
# _is_dangerous_cmd
# ---------------------------------------------------------------------------


class TestIsDangerousCmd:
    @pytest.mark.parametrize(
        "cmd",
        [
            ["sudo", "rm", "-rf", "/"],
            ["su", "root"],
            ["runas", "/user:admin", "cmd"],
            ["shutdown", "-h", "now"],
            ["halt"],
            ["poweroff"],
            ["reboot"],
            ["format", "C:"],
        ],
    )
    def test_unconditional_block(self, cmd: list[str]) -> None:
        reason = _is_dangerous_cmd(cmd)
        assert reason is not None, f"{cmd} should be blocked"

    @pytest.mark.parametrize(
        "cmd",
        [
            ["bash", "-c", "rm -rf /"],
            ["sh", "-c", "echo pwned"],
            ["python", "-c", "import os; os.system('rm -rf /')"],
            ["python3", "-c", "print('hello')"],
            ["node", "-e", "require('child_process').exec('rm -rf /')"],
            ["perl", "-e", "system('rm -rf /')"],
            ["ruby", "-e", "puts 'hello'"],
            ["cmd", "/c", "del /f /s /q C:\\"],
            ["powershell", "-command", "Remove-Item -Recurse -Force C:\\"],
            ["pwsh", "-Command", "Remove-Item /"],
        ],
    )
    def test_arg_level_block(self, cmd: list[str]) -> None:
        reason = _is_dangerous_cmd(cmd)
        assert reason is not None, f"{cmd} should be blocked"

    @pytest.mark.parametrize(
        "cmd",
        [
            ["python", "script.py"],
            ["python3", "-m", "pytest"],
            ["node", "server.js"],
            ["bash", "deploy.sh"],
            ["git", "status"],
            ["npm", "install"],
            ["docker", "ps"],
            ["echo", "hello"],
            ["ls", "-la"],
        ],
    )
    def test_safe_commands_allowed(self, cmd: list[str]) -> None:
        reason = _is_dangerous_cmd(cmd)
        assert reason is None, f"{cmd} should be allowed, but: {reason}"

    def test_case_insensitive(self) -> None:
        assert _is_dangerous_cmd(["SUDO", "ls"]) is not None
        assert _is_dangerous_cmd(["Python", "-c", "print(1)"]) is not None

    def test_path_prefix_stripped(self) -> None:
        assert _is_dangerous_cmd(["/usr/bin/sudo", "ls"]) is not None
        assert _is_dangerous_cmd(["C:\\Python312\\python.exe", "-c", "1"]) is not None

    def test_windows_exec_extension_stripped_on_all_platforms(self) -> None:
        """Known Windows executable extensions are recognised off-Windows too.

        An agent running on Linux may still be asked to run a Windows-style
        argv; the danger check must not depend on the host platform.
        """
        assert _is_dangerous_cmd(["python.exe", "-c", "1"]) is not None
        assert _is_dangerous_cmd([".\\python.bat", "-c", "1"]) is not None
        assert _is_dangerous_cmd(["sudo.cmd", "ls"]) is not None
        # POSIX dotted names carry no Windows extension — left intact.
        assert _is_dangerous_cmd(["python3.12", "--version"]) is None

    def test_empty_cmd_safe(self) -> None:
        assert _is_dangerous_cmd([]) is None


# ---------------------------------------------------------------------------
# Argument limits (via ExecCommandTool.run)
# ---------------------------------------------------------------------------


class TestArgLimits:
    @pytest.mark.asyncio
    async def test_too_many_args(self) -> None:
        tool = ExecCommandTool()
        cmd = ["echo"] + [f"arg{i}" for i in range(_MAX_ARGS)]
        result = await tool.run(cmd=cmd)
        assert not result.success
        assert "too many arguments" in result.error

    @pytest.mark.asyncio
    async def test_arg_too_long(self) -> None:
        tool = ExecCommandTool()
        cmd = ["echo", "x" * (_MAX_ARG_LEN + 1)]
        result = await tool.run(cmd=cmd)
        assert not result.success
        assert "exceeds max length" in result.error


# ---------------------------------------------------------------------------
# Integration: ExecCommandTool uses safe env
# ---------------------------------------------------------------------------


class TestExecCommandHardened:
    @pytest.mark.asyncio
    async def test_denied_command_returns_fail(self) -> None:
        tool = ExecCommandTool()
        result = await tool.run(cmd=["sudo", "ls"])
        assert not result.success
        assert "blocked" in result.error.lower()

    @pytest.mark.asyncio
    async def test_env_sanitized_in_child(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A child process should not see leaked API keys."""
        monkeypatch.setenv("MY_SECRET_TOKEN", "super-secret-value")
        tool = ExecCommandTool()
        # On Windows, use `echo %MY_SECRET_TOKEN%` or `set` to list env.
        # On Unix, use `env` or `printenv`.
        import sys
        if sys.platform == "win32":
            cmd = ["cmd", "/c", "echo", "%MY_SECRET_TOKEN%"]
        else:
            cmd = ["printenv", "MY_SECRET_TOKEN"]
        # This should be blocked by cmd deny list on Windows (cmd /c).
        # On Unix, printenv is allowed — check it doesn't see the var.
        if sys.platform == "win32":
            result = await tool.run(cmd=cmd)
            # cmd /c should be blocked
            assert not result.success
        else:
            result = await tool.run(cmd=cmd)
            # If the var is in safe env, that's a bug.
            # It should not be because MY_SECRET_TOKEN doesn't match whitelist.
            # printenv might return empty or fail — either way, no secret.
            if result.success:
                assert "super-secret-value" not in (result.output or {}).get("stdout", "")
