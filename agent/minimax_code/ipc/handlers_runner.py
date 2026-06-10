"""JSON-RPC handlers for external agent runner discovery and launch.

The runner layer is the product-facing contract above command sessions.
The built-in ``native`` runner delegates to terminal sessions, while
external CLI runners translate a prompt plus explicit local safety
settings into a concrete command line.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .handler_utils import HandlerError
from .handlers_terminal import default_working_directory, start_terminal_command
from .protocol import INVALID_PARAMS
from .server import Context

_RUNNER_ERROR = -32000
_MAX_COMMAND_LEN = 4_000
_DEFAULT_TIMEOUT_S = 10 * 60
_MAX_TIMEOUT_S = 60 * 60
_PROBE_TIMEOUT_S = 5
_CODEX_SANDBOX_MODES = {"read-only", "workspace-write", "danger-full-access"}
_CODEX_APPROVAL_POLICIES = {"untrusted", "on-failure", "on-request", "never"}
_CLAUDE_PERMISSION_MODES = {"default", "acceptEdits", "bypassPermissions", "plan"}


def _shell_command(args: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    import shlex

    return shlex.join(args)


def _run_probe(path: str, args: list[str]) -> tuple[bool, str | None, str | None]:
    command = [path, *args]
    try:
        is_cmd = Path(path).suffix.lower() in {".cmd", ".bat"}
        result = subprocess.run(
            _shell_command(command) if is_cmd else command,
            shell=is_cmd,
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
            check=False,
        )
    except PermissionError as exc:
        return False, None, f"not runnable: {exc.strerror or 'permission denied'}"
    except Exception as exc:
        return False, None, f"not runnable: {exc}"
    text = (result.stdout or result.stderr or "").strip()
    version = text.splitlines()[0].strip() if text else None
    if result.returncode != 0:
        return False, version, text or f"probe exited with code {result.returncode}"
    return True, version, None


def _runner_entry(
    *,
    id: str,
    label: str,
    command_name: str | None,
    version_args: list[str] | None,
    supports_prompt: bool,
) -> dict[str, Any]:
    if command_name is None:
        return {
            "id": id,
            "label": label,
            "kind": "native",
            "available": True,
            "command": None,
            "version": None,
            "reason": None,
            "supports_prompt": False,
            "supports_terminal": True,
        }
    path = shutil.which(command_name)
    if not path:
        return {
            "id": id,
            "label": label,
            "kind": "external_cli",
            "available": False,
            "command": None,
            "version": None,
            "reason": f"{command_name} executable not found on PATH",
            "supports_prompt": supports_prompt,
            "supports_terminal": True,
        }
    ok, version, reason = _run_probe(path, version_args or ["--version"])
    return {
        "id": id,
        "label": label,
        "kind": "external_cli",
        "available": ok,
        "command": path,
        "version": version,
        "reason": reason,
        "supports_prompt": supports_prompt,
        "supports_terminal": True,
    }


def _runner_catalog() -> list[dict[str, Any]]:
    return [
        _runner_entry(
            id="native",
            label="Native shell",
            command_name=None,
            version_args=None,
            supports_prompt=False,
        ),
        _runner_entry(
            id="codex-cli",
            label="Codex CLI",
            command_name="codex",
            version_args=["--version"],
            supports_prompt=True,
        ),
        _runner_entry(
            id="claude-code-cli",
            label="Claude Code CLI",
            command_name="claude",
            version_args=["--version"],
            supports_prompt=True,
        ),
    ]


def _require_params(params: Any) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise HandlerError(INVALID_PARAMS, "params must be a JSON object")
    return params


def _runner_id_from_params(params: dict[str, Any]) -> str:
    runner_id = params.get("runner_id", "native")
    if not isinstance(runner_id, str) or not runner_id:
        raise HandlerError(INVALID_PARAMS, "'runner_id' must be a non-empty string")
    known = {runner["id"] for runner in _runner_catalog()}
    if runner_id not in known:
        raise HandlerError(INVALID_PARAMS, f"unknown runner_id: {runner_id!r}")
    return runner_id


def _command_from_params(params: dict[str, Any]) -> str:
    command = params.get("command")
    if not isinstance(command, str) or not command.strip():
        raise HandlerError(INVALID_PARAMS, "'command' must be a non-empty string")
    command = command.strip()
    if len(command) > _MAX_COMMAND_LEN:
        raise HandlerError(INVALID_PARAMS, f"'command' must be <= {_MAX_COMMAND_LEN} chars")
    return command


def _cwd_from_params(params: dict[str, Any]) -> str:
    raw = params.get("cwd")
    if raw is None or raw == "":
        return default_working_directory()
    if not isinstance(raw, str):
        raise HandlerError(INVALID_PARAMS, "'cwd' must be a string when provided")
    path = Path(raw).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise HandlerError(INVALID_PARAMS, "'cwd' must point to an existing directory")
    return str(path)


def _timeout_from_params(params: dict[str, Any]) -> float:
    raw = params.get("timeout_s", _DEFAULT_TIMEOUT_S)
    if not isinstance(raw, (int, float)) or raw <= 0:
        raise HandlerError(INVALID_PARAMS, "'timeout_s' must be a positive number")
    return float(min(raw, _MAX_TIMEOUT_S))


def _chat_session_id_from_params(params: dict[str, Any]) -> str | None:
    raw = params.get("session_id")
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        raise HandlerError(INVALID_PARAMS, "'session_id' must be a string when provided")
    return raw


def _choice_from_params(
    params: dict[str, Any],
    key: str,
    allowed: set[str],
    default: str,
) -> str:
    raw = params.get(key, default)
    if raw is None or raw == "":
        return default
    if not isinstance(raw, str) or raw not in allowed:
        values = ", ".join(sorted(allowed))
        raise HandlerError(INVALID_PARAMS, f"'{key}' must be one of: {values}")
    return raw


def _runner_command(runner: dict[str, Any], prompt: str, params: dict[str, Any]) -> str:
    command = runner.get("command")
    if not isinstance(command, str) or not command:
        raise HandlerError(
            _RUNNER_ERROR,
            f"{runner['label']} is not runnable",
            {"runner_id": runner["id"], "reason": runner.get("reason")},
        )
    if runner["id"] == "codex-cli":
        sandbox_mode = _choice_from_params(
            params,
            "sandbox_mode",
            _CODEX_SANDBOX_MODES,
            "workspace-write",
        )
        approval_policy = _choice_from_params(
            params,
            "approval_policy",
            _CODEX_APPROVAL_POLICIES,
            "never",
        )
        return _shell_command(
            [
                command,
                "exec",
                "--sandbox",
                sandbox_mode,
                "--ask-for-approval",
                approval_policy,
                prompt,
            ]
        )
    if runner["id"] == "claude-code-cli":
        permission_mode = _choice_from_params(
            params,
            "permission_mode",
            _CLAUDE_PERMISSION_MODES,
            "acceptEdits",
        )
        args = [command, "--print"]
        if permission_mode != "default":
            args.extend(["--permission-mode", permission_mode])
        args.append(prompt)
        return _shell_command(args)
    return prompt


def register_runner_handlers(server: Any) -> None:
    """Register ``runner.*`` handlers."""

    async def handle_list(params: Any, ctx: Context) -> None:
        try:
            await ctx.reply({"runners": _runner_catalog()})
        except Exception as exc:
            await ctx.reply_error(_RUNNER_ERROR, "runner.list failed", {"error": str(exc)})

    async def handle_start(params: Any, ctx: Context) -> None:
        try:
            p = _require_params(params)
            runner_id = _runner_id_from_params(p)
            runner = next(item for item in _runner_catalog() if item["id"] == runner_id)
            if runner_id != "native" and not runner["available"]:
                raise HandlerError(
                    _RUNNER_ERROR,
                    f"{runner['label']} is not runnable",
                    {
                        "runner_id": runner_id,
                        "available": runner["available"],
                        "reason": runner["reason"],
                    },
            )
            command = _command_from_params(p)
            terminal_command = command if runner_id == "native" else _runner_command(runner, command, p)
            session = await start_terminal_command(
                command=terminal_command,
                cwd=_cwd_from_params(p),
                timeout_s=_timeout_from_params(p),
                chat_session_id=_chat_session_id_from_params(p),
                emit=ctx.emit,
            )
            await ctx.reply({"runner": runner, "session": session.to_wire()})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:
            await ctx.reply_error(_RUNNER_ERROR, "runner.start failed", {"error": str(exc)})

    server.register("runner.list", handle_list)
    server.register("runner.start", handle_start)


__all__ = ["register_runner_handlers"]
