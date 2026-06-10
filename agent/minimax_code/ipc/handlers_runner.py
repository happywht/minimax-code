"""JSON-RPC handlers for external agent runner discovery and launch.

The runner layer is the product-facing contract above command sessions.
P1 starts conservatively: the built-in ``native`` runner delegates to
the existing terminal command session, while external CLIs are exposed
as detectable but not yet executable adapters.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .handler_utils import HandlerError
from .handlers_terminal import start_terminal_command
from .protocol import INVALID_PARAMS
from .server import Context

_RUNNER_ERROR = -32000
_MAX_COMMAND_LEN = 4_000
_DEFAULT_TIMEOUT_S = 10 * 60
_MAX_TIMEOUT_S = 60 * 60


def _runner_catalog() -> list[dict[str, Any]]:
    codex_path = shutil.which("codex")
    claude_path = shutil.which("claude")
    return [
        {
            "id": "native",
            "label": "Native shell",
            "kind": "native",
            "available": True,
            "command": None,
            "version": None,
            "reason": None,
            "supports_prompt": False,
            "supports_terminal": True,
        },
        {
            "id": "codex-cli",
            "label": "Codex CLI",
            "kind": "external_cli",
            "available": bool(codex_path),
            "command": codex_path,
            "version": None,
            "reason": None if codex_path else "codex executable not found on PATH",
            "supports_prompt": True,
            "supports_terminal": True,
        },
        {
            "id": "claude-code-cli",
            "label": "Claude Code CLI",
            "kind": "external_cli",
            "available": bool(claude_path),
            "command": claude_path,
            "version": None,
            "reason": None if claude_path else "claude executable not found on PATH",
            "supports_prompt": True,
            "supports_terminal": True,
        },
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
        return str(Path.cwd())
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
            if runner_id != "native":
                raise HandlerError(
                    _RUNNER_ERROR,
                    f"{runner['label']} adapter is detected but not executable yet",
                    {
                        "runner_id": runner_id,
                        "available": runner["available"],
                        "reason": runner["reason"],
                    },
                )
            command = _command_from_params(p)
            session = await start_terminal_command(
                command=command,
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
